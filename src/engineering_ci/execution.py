"""Execute a selection plan against its bound Git snapshot.

This is the host/Actions consumer of the plan. It never re-selects tests: it
runs exactly the plan targets, verifies the checkout tree for immutable
snapshots, and records native results separately from selection. Selected
Hygiene and source checks run even when no business tests were chosen.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from . import (
    check_contract,
    expected_checks,
    git_source,
    hygiene,
    native_producer,
    plan_contract,
    security_policy,
    selection,
    sources,
    workflow_analysis,
)
from ._paths import SourcePathError, source_file_present

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-execution-report"
_HYGIENE_CHECKS = frozenset(
    {"docs", "tach", "tach_external", "vulture", "whitespace", "forwarding"}
)
_WORKFLOW_SCRIPT_CHECKS = frozenset(
    {"actionlint", "shellcheck", "workflow_shellcheck", "zizmor"}
)


class ExecutionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_plan(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ExecutionError("plan_unreadable") from None
    return validate_plan(value)


def validate_plan(value):
    """One public validation path, including direct API and Dagger callers."""
    try:
        return plan_contract.validate(value)
    except plan_contract.PlanError as error:
        raise ExecutionError(error.code) from None


def plan_rejection(error):
    return {
        "kind": REPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "status": "not_executed",
        "reason": error.code,
        "exit_code": 2,
        "tests": {"status": "not_executed"},
        "checks": [],
        "prerequisites": [],
        "plan_declaration_consistent": False,
        "source_binding_verified": "unknown",
        "trusted": False,
    }


def _selected_checks(plan):
    chosen = plan["selection"]
    selected = [
        item
        for item in plan.get("check_scope", chosen.get("check_scope", []))
        if item.get("status") in {"selected", "unknown"}
    ]
    if ("check_scope" not in plan and "check_scope" not in chosen) and chosen.get(
        "checks"
    ):
        selected = [{"name": name, "status": "unknown"} for name in chosen["checks"]]
    return selected


def _config_path(plan, root, override, *, key, default_name):
    if override is not None:
        path = Path(override)
        return path if path.is_absolute() else root / path
    inputs = plan.get("inputs") or {}
    declared = inputs.get(key) if isinstance(inputs, dict) else None
    if isinstance(declared, dict) and declared.get("path"):
        path = Path(declared["path"])
        return path if path.is_absolute() else root / path
    return root / default_name


def _rebased_consumer_plan(plan, source_root):
    """Rebase repo-local absolute config paths to repo-relative for consumer views."""
    from pathlib import PurePosixPath, PureWindowsPath

    inputs = plan.get("inputs")
    if not isinstance(inputs, dict):
        return plan
    resolved_src = Path(source_root).resolve()
    declared_root = (plan.get("git") or {}).get("repo_root")
    new_inputs = None
    for key in ("config", "hygiene", "hygiene_config", "sources", "sources_config"):
        entry = inputs.get(key)
        if not isinstance(entry, dict) or entry.get("policy_scope") == "event_external":
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        rel_path = None
        cand = Path(raw_path)
        if cand.is_absolute():
            try:
                resolved = cand.resolve()
            except OSError:
                resolved = None
            if resolved is not None and resolved.is_relative_to(resolved_src):
                rel_path = resolved.relative_to(resolved_src).as_posix()
        if rel_path is None and isinstance(declared_root, str) and declared_root:
            for pure_cls in (PureWindowsPath, PurePosixPath):
                p_root = pure_cls(declared_root)
                p_cand = pure_cls(raw_path)
                if (
                    p_root.is_absolute()
                    and p_cand.is_absolute()
                    and p_cand.is_relative_to(p_root)
                ):
                    rel_path = p_cand.relative_to(p_root).as_posix()
                    break
        if rel_path is not None:
            if new_inputs is None:
                new_inputs = dict(inputs)
            new_inputs[key] = {
                **entry,
                "path": rel_path,
            }
    if new_inputs is None:
        return plan
    return {**plan, "inputs": new_inputs}


def _rebased_consumer_arg_path(value, source_root, view_root):
    """Rebase explicit repo-local config path parameters onto a Consumer View."""
    if not isinstance(value, (str, os.PathLike)):
        return value
    raw = Path(value)
    if not raw.is_absolute():
        return value
    try:
        resolved = raw.resolve()
        src_resolved = Path(source_root).resolve()
    except OSError:
        return value
    if resolved.is_relative_to(src_resolved):
        return Path(view_root) / resolved.relative_to(src_resolved)
    return value


def _consumer_view_binding_matches(binding, source_root, view_root, actual):
    """Allow only the absolute-link rewrite performed when copying a Source."""
    paths = binding.get("paths")
    if not isinstance(paths, dict):
        return False
    source_root = Path(source_root).resolve()
    view_root = Path(view_root).resolve()
    if selection.worktree_content_binding(source_root, paths) != binding:
        return False
    for key in ("algorithm", "paths", "unverified_paths"):
        if actual.get(key) != binding.get(key):
            return False
    planned_links = binding.get("link_bindings") or {}
    actual_links = actual.get("link_bindings") or {}
    if planned_links.keys() != actual_links.keys():
        return False
    for path, planned in planned_links.items():
        observed = actual_links[path]
        if planned.get("target_path") != observed.get("target_path"):
            return False
        planned_components = planned.get("components", [])
        observed_components = observed.get("components", [])
        if len(planned_components) != len(observed_components):
            return False
        for before, after in zip(planned_components, observed_components, strict=True):
            component = before.get("path")
            if component != after.get("path"):
                return False
            if before.get("sha256") == after.get("sha256"):
                continue
            try:
                source_target = os.readlink(source_root / component)
                view_target = os.readlink(view_root / component)
                target = Path(os.path.normpath(source_target))
                if not target.is_absolute() or not target.is_relative_to(source_root):
                    return False
                expected = os.path.relpath(
                    view_root / target.relative_to(source_root),
                    (view_root / component).parent,
                )
                if (
                    hashlib.sha256(os.fsencode(source_target)).hexdigest()
                    != before.get("sha256")
                    or hashlib.sha256(os.fsencode(view_target)).hexdigest()
                    != after.get("sha256")
                    or os.fsencode(view_target) != os.fsencode(expected)
                ):
                    return False
            except (OSError, TypeError, ValueError):
                return False
    return True


def _stored_rule_path(config_path, root):
    resolved = Path(config_path).resolve()
    resolved_root = Path(root).resolve()
    try:
        if resolved.is_relative_to(resolved_root):
            return resolved.relative_to(resolved_root).as_posix()
    except OSError:
        pass
    return str(config_path)


def _rule_ref_with_path(name, version, config_path, root, digest):
    return check_contract.rule_ref(
        name,
        version,
        path=_stored_rule_path(config_path, root),
        digest=digest,
    )


def _resolved_rule_and_input(
    identity_fn, error_type, config_path, config, root, *, fallback_name, digest_after
):
    rel_path = _stored_rule_path(config_path, root)
    config_input = {"kind": "config", "path": rel_path, "sha256": digest_after}
    identity = None
    if hasattr(config, "schema_version"):
        try:
            identity = identity_fn(config_path, config, root=root)
        except (OSError, error_type):
            identity = None
    eff_ref = (identity or {}).get("effective_check_config_ref")
    if isinstance(eff_ref, dict):
        return dict(eff_ref), config_input
    return (
        _rule_ref_with_path(
            fallback_name,
            getattr(config, "schema_version", 1),
            config_path,
            root,
            digest_after,
        ),
        config_input,
    )


def _hygiene_config_path(plan, root, hygiene_config):
    return _config_path(
        plan, root, hygiene_config, key="hygiene", default_name="ci-hygiene.toml"
    )


def _sources_config_path(plan, root, sources_config):
    return _config_path(
        plan, root, sources_config, key="sources", default_name="ci-sources.toml"
    )


def _normalized_workflow_config_snapshot(digests):
    if not isinstance(digests, dict):
        return None
    normalized = {}
    for rel_path, entry in sorted(digests.items()):
        if isinstance(entry, dict):
            normalized[str(rel_path)] = (
                entry.get("status"),
                entry.get("sha256"),
                entry.get("reason"),
            )
        elif isinstance(entry, str):
            normalized[str(rel_path)] = ("legacy", entry, None)
        else:
            normalized[str(rel_path)] = ("invalid", None, None)
    return normalized


def _workflow_config_drift_reason(plan, root, selection_config=None):
    """Fail closed if tracked workflow/script tool configs changed after Plan creation."""
    inputs = plan.get("inputs") or {}
    declared_cfg = inputs.get("config") if isinstance(inputs, dict) else None
    chosen = plan.get("selection") if isinstance(plan, dict) else None
    check_names = set()
    if isinstance(chosen, dict):
        check_names.update(
            item for item in chosen.get("checks") or () if isinstance(item, str)
        )
        for source in (chosen.get("check_scope"), plan.get("check_scope")):
            for item in source or ():
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    check_names.add(item["name"])
    needs_snapshot = bool(check_names & plan_contract.WORKFLOW_SCRIPT_CHECK_NAMES)
    if (
        not isinstance(declared_cfg, dict)
        or "workflow_config_digests" not in declared_cfg
    ):
        return "workflow_config_snapshot_invalid" if needs_snapshot else None
    declared_norm = _normalized_workflow_config_snapshot(
        declared_cfg.get("workflow_config_digests")
    )
    if declared_norm is None:
        return "workflow_config_snapshot_invalid"
    wf_obj = (
        getattr(selection_config, "workflow_checks", None)
        if selection_config is not None
        else None
    )
    norm_cfg = (declared_cfg.get("normalized") or {}).get("workflow_checks") or {}
    admitted = declared_cfg.get("workflow_external_configs")
    current = workflow_analysis.workflow_config_digests(
        root,
        wf_obj,
        actionlint_config=str(norm_cfg.get("actionlint_config") or ""),
        shellcheck_config=str(norm_cfg.get("shellcheck_config") or ""),
        zizmor_config=str(norm_cfg.get("zizmor_config") or ""),
        admitted_external_configs=admitted,
    )
    if _normalized_workflow_config_snapshot(current) != declared_norm:
        return "workflow_config_snapshot_stale"
    return None


def _verify_execution_binding(plan, root, *, execution_context="host"):
    """Return (ok, reason, expected_tree). ok False means refuse execution."""
    ok, reason, expected_tree = _verify_git_execution_binding(
        plan, root, execution_context=execution_context
    )
    if not ok:
        return False, reason, expected_tree
    wf_reason = _workflow_config_drift_reason(plan, root)
    if wf_reason is not None:
        return False, wf_reason, expected_tree
    return True, None, expected_tree


def _verify_git_execution_binding(plan, root, *, execution_context="host"):
    git = plan["git"]
    expected_tree = git.get("execution_tree")
    explicit_snapshot = git.get("snapshot")
    snapshot = explicit_snapshot
    if snapshot is not None and (
        not isinstance(snapshot, str) or snapshot not in plan_contract.GIT_SNAPSHOTS
    ):
        return False, "git_snapshot_unsupported", expected_tree
    if snapshot is None:
        snapshot = "commit" if expected_tree else "worktree"
    if snapshot == "index":
        execution_tree = git.get("execution_tree")
        index_tree = git.get("index_tree")
        if execution_tree and index_tree and execution_tree != index_tree:
            return False, "index_source_identity_mismatch", execution_tree
        if execution_context == "dagger_export":
            source_tree_id = git.get("source_tree_id")
            if not execution_tree or not index_tree or not source_tree_id:
                return False, "dagger_export_index_source_unverified", execution_tree
            if selection.directory_content_id(root) != source_tree_id:
                return False, "source_tree_mismatch", execution_tree
            # This proves only the exported bytes. Git-dependent checks still
            # need the actual comparison baseline and object database.
            return True, None, execution_tree
        expected_tree = expected_tree or index_tree
        if (
            git.get("base_tree") is not None
            and selection.unborn_index_base(root, base=git.get("base_ref"))
            != git["base_tree"]
        ):
            return False, "index_unborn_baseline_stale", expected_tree
        refusal = selection.index_source_refusal(
            root, expected_tree, index_file=git.get("index_file")
        )
        if refusal:
            return False, refusal, expected_tree
        return True, None, expected_tree
    if snapshot == "worktree":
        binding = git.get("content_binding")
        if isinstance(binding, dict) and binding.get("digest"):
            current = selection.worktree_content_binding(
                root, git.get("changed_paths") or ()
            )
            if current.get("digest") != binding.get("digest"):
                return False, "worktree_snapshot_stale", None
        return True, None, None
    if snapshot == "multi":
        return False, "multi_ref_requires_per_ref_execution", None
    if snapshot == "push" and git.get("ref_action") == "delete":
        if expected_tree or git.get("tree") or git.get("head_commit"):
            return False, "deleted_ref_forged_target_tree", expected_tree
        return False, "deleted_ref_no_execution_tree", None
    if snapshot == "pr_merge":
        merge_tree = git.get("merge_tree")
        if expected_tree and merge_tree and expected_tree != merge_tree:
            return False, "pr_merge_tree_mismatch", expected_tree
        expected_tree = expected_tree or merge_tree
        if not expected_tree:
            return False, "pr_merge_tree_missing", None
    elif snapshot in {"commit", "push"}:
        tree_field = git.get("tree")
        if expected_tree and tree_field and expected_tree != tree_field:
            return False, "target_tree_mismatch", expected_tree
        expected_tree = expected_tree or tree_field
        if not expected_tree and explicit_snapshot in {"commit", "push"}:
            return False, "target_tree_missing", None
    if expected_tree:
        has_git_root = (
            execution_context != "dagger_export"
            and selection._resolve_git_dir(root) is not None
        )
        if not has_git_root:
            source_tree_id = git.get("source_tree_id")
            if source_tree_id:
                if selection.directory_content_id(root) != source_tree_id:
                    return False, "source_tree_mismatch", expected_tree
                return True, None, expected_tree
            in_parent_repo = False
            if execution_context != "dagger_export":
                top = selection._git(root, "rev-parse", "--show-toplevel")
                in_parent_repo = bool(top and top.returncode == 0)
            if (
                explicit_snapshot is not None
                or in_parent_repo
                or execution_context == "dagger_export"
            ):
                reason = (
                    "dagger_export_source_identity_missing"
                    if execution_context == "dagger_export"
                    else "source_git_root_mismatch"
                )
                return False, reason, expected_tree
        actual = selection._tree_of(root, "HEAD")
        if actual != expected_tree:
            reason = (
                "pr_merge_tree_unmaterialized"
                if snapshot == "pr_merge"
                else "tree_mismatch"
            )
            return False, reason, expected_tree
        head_commit = git.get("head_commit")
        if snapshot in {"commit", "push"} and head_commit:
            actual_head = selection._resolve(root, "HEAD")
            if actual_head != head_commit:
                return False, "head_commit_mismatch", expected_tree
        if has_git_root and snapshot in {"commit", "push", "pr_merge"}:
            refusal = selection.worktree_tree_refusal(root, expected_tree)
            if refusal:
                return False, refusal, expected_tree
    return True, None, expected_tree


def _changeset_from_plan(plan, root, *, extra_paths=()):
    git = plan["git"]
    target_tree = git.get("execution_tree")
    snapshot = str(git.get("snapshot") or ("commit" if target_tree else "worktree"))
    paths = tuple(git.get("changed_paths") or ())
    deleted = tuple(git.get("deleted_paths") or ())
    generated = tuple(
        sorted(
            {
                str(p).replace("\\", "/")
                for p in extra_paths
                if isinstance(p, str) and p.strip()
            }
        )
    )
    complete = (
        bool(git.get("base_commit") or git.get("base_tree"))
        or git.get("ref_action") == "delete"
    ) and "changed_paths" in git
    return selection.ChangeSet(
        root=Path(root),
        base_ref=str(git.get("base_ref") or ""),
        head_ref=str(git.get("head_ref") or "HEAD"),
        base_commit=git.get("base_commit"),
        head_commit=git.get("head_commit"),
        tree=git.get("tree")
        or (target_tree if snapshot in {"commit", "push"} else None),
        paths=paths,
        worktree_dirty=bool(git.get("worktree_dirty")),
        complete=complete,
        error=None if complete else "incomplete_plan_git",
        snapshot=snapshot,
        index_tree=git.get("index_tree")
        or (target_tree if snapshot == "index" else None),
        index_file=git.get("index_file"),
        merge_tree=git.get("merge_tree")
        or (target_tree if snapshot == "pr_merge" else None),
        base_tip=git.get("base_tip"),
        deleted_paths=deleted,
        generated_paths=generated,
        ref_action=git.get("ref_action"),
        object_type=git.get("object_type"),
        force_push=bool(git.get("force_push", False)),
        baseline_status=git.get("baseline_status"),
        base_tree=git.get("base_tree"),
    )


def _is_hex64(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _is_git_oid(value):
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _admitted_external_config(
    declared, resolved_path, *, ref_key="effective_check_config_ref"
):
    if (
        not isinstance(declared, dict)
        or declared.get("policy_scope") != "event_external"
    ):
        return False
    raw_path = declared.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    declared_path = Path(raw_path)
    if not declared_path.is_absolute():
        return False
    try:
        if declared_path.resolve() != resolved_path:
            return False
    except OSError:
        return False
    if not _is_hex64(declared.get("sha256")):
        return False
    eff_ref = declared.get(ref_key)
    return isinstance(eff_ref, dict) and isinstance(eff_ref.get("sha256"), str)


def _load_verified_config(
    plan,
    root,
    override,
    *,
    key,
    default_name,
    loader,
    error_type,
    identity_fn,
):
    resolved_root = Path(root).resolve()
    inputs = plan.get("inputs") or {}
    declared = inputs.get(key) if isinstance(inputs, dict) else None
    config_path = _config_path(
        plan, resolved_root, override, key=key, default_name=default_name
    )
    try:
        resolved_path = config_path.resolve()
    except OSError:
        return None, None, None, f"{key}_config_read_failed"
    if not resolved_path.is_relative_to(
        resolved_root
    ) and not _admitted_external_config(declared, resolved_path):
        return None, None, None, f"{key}_config_outside_root"
    if override is not None and isinstance(declared, dict) and declared.get("path"):
        declared_raw = Path(declared["path"])
        declared_resolved = (
            declared_raw if declared_raw.is_absolute() else resolved_root / declared_raw
        ).resolve()
        if declared_resolved != resolved_path:
            return None, None, None, f"{key}_config_override_mismatch"
    try:
        digest_before = check_contract.file_digest(resolved_path)
        loaded = loader(resolved_path)
        digest_after = check_contract.file_digest(resolved_path)
    except (OSError, error_type) as error:
        code = getattr(error, "code", "read_failed")
        return None, None, None, f"{key}_config_{code}"
    if digest_before != digest_after:
        return None, None, None, f"{key}_config_changed_during_read"
    if isinstance(declared, dict):
        if declared.get("sha256") is not None and digest_after != declared["sha256"]:
            return None, None, None, f"{key}_config_digest_mismatch"
        if (
            declared.get("normalized") is not None
            or declared.get("effective_check_config_ref") is not None
        ):
            actual_identity = identity_fn(resolved_path, loaded, root=resolved_root)
            if (
                declared.get("normalized") is not None
                and actual_identity.get("normalized") != declared["normalized"]
            ) or (
                declared.get("effective_check_config_ref") is not None
                and actual_identity.get("effective_check_config_ref")
                != declared["effective_check_config_ref"]
            ):
                return None, None, None, f"{key}_config_identity_mismatch"
    return loaded, resolved_path, digest_after, None


def _hygiene_failure_results(wanted, selected_by_name, reason):
    return [
        {
            "name": name,
            "selected": selected_by_name[name].get("status"),
            "status": "failed",
            "reason": reason,
            "execution_started": False,
            "execution_kind": "not_started",
        }
        for name in wanted
    ], True


def _resolved_code_prefixes(plan, root):
    selection_block = plan.get("selection") or {}
    code_prefixes = selection_block.get("code_prefixes")
    if code_prefixes is not None and not (
        isinstance(code_prefixes, list)
        and all(isinstance(item, str) and item for item in code_prefixes)
    ):
        return None, "selection_code_prefixes_invalid"
    resolved_root = Path(root).resolve()
    inputs = plan.get("inputs") or {}
    sel_decl = inputs.get("config") if isinstance(inputs, dict) else None
    sel_path = resolved_root / "ci-selection.toml"
    external_selection = False
    if isinstance(sel_decl, dict) and sel_decl.get("path"):
        raw_sel = Path(sel_decl["path"])
        cand_path = raw_sel if raw_sel.is_absolute() else resolved_root / raw_sel
        try:
            resolved_sel = cand_path.resolve()
        except OSError:
            return None, "selection_config_read_failed"
        if not resolved_sel.is_relative_to(resolved_root):
            if not _admitted_external_config(
                sel_decl, resolved_sel, ref_key="effective_config_ref"
            ):
                return None, "selection_config_outside_root"
            sel_path = resolved_sel
            external_selection = True
    gen_sha = (plan.get("generator") or {}).get("config_sha256")
    if external_selection:
        if isinstance(gen_sha, str) and sel_decl.get("sha256") != gen_sha:
            return None, "selection_config_digest_mismatch"
        if (
            sel_path.is_file()
            and check_contract.file_digest(sel_path) != sel_decl["sha256"]
        ):
            return None, "selection_config_digest_mismatch"
    if (
        isinstance(gen_sha, str)
        and sel_path.is_file()
        and check_contract.file_digest(sel_path) == gen_sha
    ):
        try:
            loaded_sel = selection.load_config(sel_path)
        except (OSError, selection.SelectionError):
            return None, "selection_config_invalid"
        if isinstance(sel_decl, dict) and (
            sel_decl.get("normalized") is not None
            or sel_decl.get("effective_config_ref") is not None
        ):
            actual_identity = selection.config_identity(
                sel_path, loaded_sel, root=resolved_root
            )
            if (
                sel_decl.get("normalized") is not None
                and actual_identity.get("normalized") != sel_decl["normalized"]
            ) or (
                sel_decl.get("effective_config_ref") is not None
                and actual_identity.get("effective_config_ref")
                != sel_decl["effective_config_ref"]
            ):
                return None, "selection_config_identity_mismatch"
        expected_prefixes = list(loaded_sel.check_scope.code_prefixes)
        if code_prefixes is None:
            code_prefixes = expected_prefixes
        elif list(code_prefixes) != expected_prefixes:
            return None, "selection_code_prefixes_mismatch"
    return (tuple(code_prefixes) if code_prefixes is not None else None), None


def _find_omitted_hygiene_entries(
    plan,
    selected_names,
    *,
    changes,
    config,
    root,
    code_prefixes,
    rule,
    config_input=None,
    hygiene_config_path=None,
):
    has_bound_hygiene = isinstance((plan.get("inputs") or {}).get("hygiene"), dict)
    if not has_bound_hygiene:
        return []
    expected_app = hygiene.check_applicability(
        changes,
        config,
        root=root,
        full=False,
        code_prefixes=code_prefixes,
        hygiene_config_path=hygiene_config_path,
    )
    raw_scope = {
        item.get("name"): item
        for item in (plan.get("check_scope") or ())
        if isinstance(item, dict) and item.get("name")
    }
    omitted = []
    for name in hygiene.effective_checks(config):
        if (
            expected_app.get(name, {}).get("status") in {"selected", "unknown"}
            and name not in selected_names
        ):
            entry = {
                "name": name,
                "selected": raw_scope.get(name, {}).get("status", "omitted_from_plan"),
                "status": "not_executed",
                "reason": "configured_check_omitted_from_plan",
                "execution_started": False,
                "execution_kind": "not_started",
                "rule_ref": rule,
                "plan_authorized": False,
                "routing_conflict": "configured_check_omitted_from_plan",
            }
            if config_input is not None:
                entry["config_input_ref"] = dict(config_input)
            omitted.append(entry)
    return omitted


def omitted_hygiene_checks(
    plan,
    selected,
    *,
    root,
    hygiene_config=None,
    extra_paths=(),
):
    """Return decorated not_executed entries for configured hygiene checks omitted from plan."""
    has_bound_hygiene = isinstance((plan.get("inputs") or {}).get("hygiene"), dict)
    if not has_bound_hygiene:
        return []
    code_prefixes, prefix_reason = _resolved_code_prefixes(plan, root)
    if prefix_reason is not None:
        return []
    config, config_path, digest_after, reason = _load_verified_config(
        plan,
        root,
        hygiene_config,
        key="hygiene",
        default_name="ci-hygiene.toml",
        loader=hygiene.load_config,
        error_type=hygiene.HygieneError,
        identity_fn=hygiene.config_identity,
    )
    if reason is not None:
        return []
    rule, config_input = _resolved_rule_and_input(
        hygiene.config_identity,
        hygiene.HygieneError,
        config_path,
        config,
        root,
        fallback_name="engineering-ci.hygiene",
        digest_after=digest_after,
    )
    changes = _changeset_from_plan(plan, root, extra_paths=extra_paths)
    selected_names = {
        item.get("name") for item in selected if item.get("name") in _HYGIENE_CHECKS
    }
    return _find_omitted_hygiene_entries(
        plan,
        selected_names,
        changes=changes,
        config=config,
        root=root,
        code_prefixes=code_prefixes,
        rule=rule,
        config_input=config_input,
        hygiene_config_path=config_path,
    )


def _run_hygiene_checks(
    plan,
    selected,
    *,
    root,
    hygiene_config,
    runner,
    execution_context="host",
    whitespace_runner=None,
    extra_paths=(),
    generated_input_refs=(),
    check_omitted=True,
):
    selected_by_name = {
        item["name"]: item for item in selected if item.get("name") in _HYGIENE_CHECKS
    }
    wanted = tuple(selected_by_name)
    has_bound_hygiene = isinstance((plan.get("inputs") or {}).get("hygiene"), dict)
    if not wanted and not (has_bound_hygiene and check_omitted):
        return [], False
    code_prefixes, prefix_reason = _resolved_code_prefixes(plan, root)
    if prefix_reason is not None:
        return _hygiene_failure_results(wanted, selected_by_name, prefix_reason)
    config, config_path, digest_after, reason = _load_verified_config(
        plan,
        root,
        hygiene_config,
        key="hygiene",
        default_name="ci-hygiene.toml",
        loader=hygiene.load_config,
        error_type=hygiene.HygieneError,
        identity_fn=hygiene.config_identity,
    )
    if reason is not None:
        return _hygiene_failure_results(wanted, selected_by_name, reason)
    rule, config_input = _resolved_rule_and_input(
        hygiene.config_identity,
        hygiene.HygieneError,
        config_path,
        config,
        root,
        fallback_name="engineering-ci.hygiene",
        digest_after=digest_after,
    )
    configured = hygiene.effective_checks(config)
    changes = _changeset_from_plan(plan, root, extra_paths=extra_paths)
    omitted_entries = (
        _find_omitted_hygiene_entries(
            plan,
            set(selected_by_name),
            changes=changes,
            config=config,
            root=root,
            code_prefixes=code_prefixes,
            rule=rule,
            config_input=config_input,
            hygiene_config_path=config_path,
        )
        if check_omitted
        else []
    )
    checks = tuple(name for name in configured if name in selected_by_name)
    if not checks:
        return [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "hygiene_check_disabled",
                "rule_ref": rule,
                "config_input_ref": dict(config_input),
            }
            for name, item in selected_by_name.items()
        ] + omitted_entries, False
    effective_tach = (
        dataclasses.replace(config.tach, external=False)
        if "tach_external" not in selected_by_name and config.tach.external
        else config.tach
    )
    isolated_whitespace = "whitespace" in checks and _uses_exported_index_comparison(
        plan, execution_context, extra_paths=extra_paths
    )
    base_config = dataclasses.replace(config, checks=checks, tach=effective_tach)
    remaining = (
        tuple(name for name in checks if name != "whitespace")
        if isolated_whitespace
        else checks
    )
    run_config = dataclasses.replace(base_config, checks=remaining)
    run_kwargs = {
        "root": root,
        "runner": runner or hygiene._run,
    }
    if _stored_rule_path(config_path, root) != hygiene._HYGIENE_CONFIG:
        run_kwargs["hygiene_config_path"] = config_path
    if code_prefixes is not None:
        run_kwargs["code_prefixes"] = code_prefixes
    ws_before = None
    ws_err_before = None
    ws_tree = changes.index_tree if changes.base_tree is not None else None
    if "whitespace" in remaining:
        ws_before, ws_err_before = _effective_git_whitespace_config(
            root, changes.paths, tree=ws_tree
        )
    report = (
        hygiene.run(changes, run_config, **run_kwargs) if remaining else {"checks": []}
    )
    if isolated_whitespace:
        report["checks"].insert(
            checks.index("whitespace"),
            _run_exported_index_whitespace(
                root, plan["git"], changes.paths, whitespace_runner
            ),
        )
    ws_after = None
    ws_err_after = None
    if "whitespace" in remaining:
        ws_after, ws_err_after = _effective_git_whitespace_config(
            root, changes.paths, tree=ws_tree
        )
    results = []
    seen = set()
    any_failed = report.get("status") == "failed"
    for native in report["checks"]:
        name = native["name"]
        authorized = name in selected_by_name
        payload = dict(native)
        payload["rule_ref"] = rule
        payload["config_input_ref"] = dict(config_input)
        payload["plan_authorized"] = authorized
        if not authorized:
            payload["routing_conflict"] = "unauthorized_check_executed"
        if generated_input_refs:
            payload["generated_input_refs"] = [dict(r) for r in generated_input_refs]
        if name == "docs":
            # The git inventory is a real native input step of this Check.
            payload["inventory"] = report.get("inventory")
        if name == "whitespace" and not isolated_whitespace:
            if (
                ws_err_before is None
                and ws_err_after is None
                and ws_before is not None
                and ws_before == ws_after
            ):
                payload["git_whitespace_config"] = dict(ws_before)
            else:
                payload["git_whitespace_config_drifted"] = True
        origin = selected_by_name.get(name)
        payload["selected"] = origin.get("status") if origin else "not_selected"
        if origin and isinstance(origin.get("inputs"), list):
            payload["inputs"] = list(origin["inputs"])
        if payload.get("status") == "failed":
            any_failed = True
        results.append(payload)
        seen.add(name)
    for name, item in selected_by_name.items():
        if name in seen:
            continue
        results.append(
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": (
                    "hygiene_check_disabled"
                    if name not in configured
                    else "hygiene_check_missing"
                ),
                "rule_ref": rule,
                "config_input_ref": dict(config_input),
            }
        )
    results.extend(omitted_entries)
    return results, any_failed


def _run_source_check(plan, selected, *, root, sources_config, runner):
    item = next((entry for entry in selected if entry.get("name") == "source"), None)
    if item is None:
        return None, False
    config, config_path, digest_after, reason = _load_verified_config(
        plan,
        root,
        sources_config,
        key="sources",
        default_name="ci-sources.toml",
        loader=sources.load_config,
        error_type=sources.SourcesError,
        identity_fn=sources.config_identity,
    )
    if reason is not None:
        return {
            "name": "source",
            "selected": item.get("status"),
            "status": "failed",
            "reason": reason,
            "execution_started": False,
            "execution_kind": "not_started",
        }, True
    source_runner = runner or sources._run
    report = sources.reconcile(config, root=root, runner=source_runner)
    source_rule, config_input = _resolved_rule_and_input(
        sources.config_identity,
        sources.SourcesError,
        config_path,
        config,
        root,
        fallback_name="engineering-ci.sources",
        digest_after=digest_after,
    )
    payload = {
        "name": "source",
        "selected": item.get("status"),
        "status": report.status,
        "reason": None if report.status == "passed" else "source_reconcile_failed",
        "counts": dict(report.counts),
        "failures": list(report.failures),
        "rule_ref": source_rule,
        "config_input_ref": dict(config_input),
    }
    if isinstance(item.get("inputs"), list):
        payload["inputs"] = list(item["inputs"])
    return payload, report.status == "failed"


def _load_optional_selection_config(plan, root, selection_config=None):
    inputs = plan.get("inputs") or {}
    declared = inputs.get("config") if isinstance(inputs, dict) else None
    default_cfg = Path(root) / "ci-selection.toml"
    if selection_config is None and declared is None and not default_cfg.is_file():
        return None, None, None
    config, config_path, digest_after, reason = _load_verified_config(
        plan,
        root,
        selection_config,
        key="config",
        default_name="ci-selection.toml",
        loader=selection.load_config,
        error_type=selection.SelectionError,
        identity_fn=selection.config_identity,
    )
    if reason is not None:
        return None, None, reason
    wf_drift = _workflow_config_drift_reason(plan, root, selection_config=config)
    if wf_drift is not None:
        return None, None, wf_drift
    stored_path = str(config_path)
    try:
        resolved_root = Path(root).resolve()
        if config_path.is_relative_to(resolved_root):
            stored_path = config_path.relative_to(resolved_root).as_posix()
    except OSError:
        pass
    rule = check_contract.rule_ref(
        "engineering-ci.selection",
        getattr(config, "schema_version", 1),
        path=stored_path,
        digest=digest_after,
    )
    return config, rule, None


def _build_workflow_admission_fn(plan, *, policy_admission=None):
    plan_adm = plan.get("policy_admission")
    effective_profile = plan.get("effective_profile")
    protected = (
        policy_admission is not None
        or plan_adm is not None
        or plan.get("protected_profile") is not None
        or (
            isinstance(effective_profile, dict)
            and bool(effective_profile.get("protected"))
        )
    )
    if not protected:
        return None
    adm = (
        policy_admission
        if isinstance(policy_admission, dict)
        else (plan_adm if isinstance(plan_adm, dict) else {})
    )
    snapshot = adm.get("snapshot")
    state_path = adm.get("state_path")
    selection_id = adm.get("selection_id") or "workflow-script-execution"
    selection_policy_revision = adm.get("selection_policy_revision")
    protected_profile = adm.get("protected_profile") or plan.get("protected_profile")
    platform = adm.get("platform") or {"os": "linux", "arch": "x86_64"}
    assets = adm.get("assets") if isinstance(adm.get("assets"), dict) else {}
    now = adm.get("now")

    def admit(check_name, access_id, version):
        if not isinstance(snapshot, dict) or not state_path:
            return {
                "decision": "unknown",
                "allowed": False,
                "reason": "policy_admission_missing",
            }
        override = assets.get(check_name)
        asset = dict(override) if isinstance(override, dict) else {}
        asset.setdefault("tool_id", check_name)
        asset.setdefault("access_id", access_id)
        asset.setdefault("version", version)
        asset.setdefault("platform", platform)
        try:
            decision = security_policy.decide(
                snapshot,
                asset,
                state_path=Path(state_path),
                selection_id=selection_id,
                selection_policy_revision=selection_policy_revision,
                protected_profile=protected_profile,
                now=now,
            )
        except security_policy.PolicyError as error:
            return {
                "decision": "unknown",
                "allowed": False,
                "reason": error.code,
            }
        if not decision.get("allowed"):
            return {
                "decision": decision.get("decision", "deny"),
                "allowed": False,
                "reason": decision.get("reason") or "policy_admission_denied",
                "rule_id": decision.get("rule_id"),
            }
        return decision

    return admit


def omitted_workflow_script_checks(
    plan,
    selected,
    *,
    root,
    selection_config=None,
    extra_paths=(),
):
    """Return decorated not_executed entries for configured workflow/script checks omitted from plan."""
    has_bound_cfg = isinstance((plan.get("inputs") or {}).get("config"), dict)
    if not has_bound_cfg:
        return []
    config, rule, reason = _load_optional_selection_config(
        plan, root, selection_config=selection_config
    )
    if reason is not None or config is None:
        return []
    cfg_input = (plan.get("inputs") or {}).get("config")
    admitted_ext = (
        cfg_input.get("workflow_external_configs")
        if isinstance(cfg_input, dict)
        else None
    )
    rule_refs = workflow_analysis.effective_workflow_rule_refs(
        rule, config, root=root, admitted_external_configs=admitted_ext
    )
    configured = set(config.checks) & _WORKFLOW_SCRIPT_CHECKS
    if not configured:
        return []
    changes = _changeset_from_plan(plan, root, extra_paths=extra_paths)
    config_path = cfg_input.get("path") if isinstance(cfg_input, dict) else None
    full = bool((plan.get("selection") or {}).get("full_tests"))
    scoped = selection.workflow_script_check_scopes(
        changes,
        config,
        root=root,
        config_path=config_path,
        full=full,
    )
    selected_names = {
        item.get("name")
        for item in selected
        if item.get("name") in _WORKFLOW_SCRIPT_CHECKS
    }
    omitted = []
    for item in scoped:
        name = item.get("name")
        if (
            name in configured
            and name not in selected_names
            and item.get("status") != "not_applicable"
        ):
            eff_rule = rule_refs.get(name) if isinstance(rule_refs, dict) else rule
            entry = {
                "name": name,
                "selected": "omitted",
                "status": "not_executed",
                "reason": "workflow_script_check_omitted_from_plan",
                "plan_authorized": False,
                "routing_conflict": "configured_check_omitted_from_plan",
                "execution_started": False,
                "execution_kind": "not_started",
            }
            if eff_rule is not None:
                entry["rule_ref"] = eff_rule
            omitted.append(entry)
    return omitted


def _run_workflow_script_checks(
    plan,
    selected,
    *,
    root,
    runner,
    selection_config=None,
    policy_admission=None,
    extra_paths=(),
    generated_input_refs=(),
    check_omitted=True,
    shared_cache=None,
    admitted_external_targets=None,
    workflow_authority=None,
    consumer_source_root=None,
):
    wanted = [item for item in selected if item.get("name") in _WORKFLOW_SCRIPT_CHECKS]
    omitted_entries = (
        omitted_workflow_script_checks(
            plan,
            selected,
            root=root,
            selection_config=selection_config,
            extra_paths=extra_paths,
        )
        if check_omitted
        else []
    )
    if not wanted:
        return omitted_entries, False
    selected_by_name = {item.get("name"): item for item in wanted}
    config, rule, reason = _load_optional_selection_config(
        plan, root, selection_config=selection_config
    )
    if reason is not None:
        return [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": reason,
                "execution_started": False,
                "execution_kind": "not_started",
            }
            for name, item in selected_by_name.items()
        ] + omitted_entries, True

    cfg_input = (plan.get("inputs") or {}).get("config")
    admitted_ext = (
        cfg_input.get("workflow_external_configs")
        if isinstance(cfg_input, dict)
        else None
    )
    rule_refs = workflow_analysis.effective_workflow_rule_refs(
        rule, config, root=root, admitted_external_configs=admitted_ext
    )
    scope_cfg = (
        config.check_scope if config is not None else selection.CheckScopeConfig()
    )
    wf_cfg = (
        config.workflow_checks
        if config is not None
        else selection.WorkflowChecksConfig()
    )
    all_selected = _selected_checks(plan)
    all_selected_names = {item.get("name") for item in all_selected}
    inline_wf_sc = next(
        (item for item in all_selected if item.get("name") == "workflow_shellcheck"),
        None,
    )
    inline_shellcheck_inputs = (
        tuple(inline_wf_sc.get("inputs") or ())
        if inline_wf_sc is not None
        and isinstance(inline_wf_sc.get("inputs"), list)
        and (
            inline_wf_sc.get("status") != "unknown" or bool(inline_wf_sc.get("inputs"))
        )
        else None
    )
    binding = plan["git"].get("content_binding")

    def binding_current():
        if not isinstance(binding, dict) or not binding.get("digest"):
            return True
        actual = selection.worktree_content_binding(root, binding.get("paths") or ())
        return actual == binding or (
            consumer_source_root is not None
            and _consumer_view_binding_matches(
                binding, consumer_source_root, root, actual
            )
        )

    if not binding_current():
        return [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "workflow_target_plan_digest_mismatch",
                "execution_started": False,
                "execution_kind": "not_started",
            }
            for name, item in selected_by_name.items()
        ] + omitted_entries, True

    suite = workflow_analysis.run_workflow_script_suite(
        selected_by_name,
        root=root,
        runner=runner,
        workflow_prefixes=tuple(scope_cfg.workflow_prefixes),
        shell_prefixes=tuple(scope_cfg.shell_prefixes),
        actionlint_config=wf_cfg.actionlint_config,
        shellcheck_config=wf_cfg.shellcheck_config,
        zizmor_config=wf_cfg.zizmor_config,
        zizmor_persona=wf_cfg.zizmor_persona,
        zizmor_offline=wf_cfg.zizmor_offline,
        shellcheck_inline=wf_cfg.shellcheck_inline,
        rule_ref=rule,
        rule_refs=rule_refs,
        extra_paths=extra_paths,
        generated_input_refs=generated_input_refs,
        admission_fn=_build_workflow_admission_fn(
            plan, policy_admission=policy_admission
        ),
        shared_cache=shared_cache,
        inline_shellcheck_requested=("workflow_shellcheck" in all_selected_names),
        inline_shellcheck_inputs=inline_shellcheck_inputs,
        admitted_external_configs=admitted_ext,
        admitted_external_targets=admitted_external_targets,
        authorize_external_target=(
            functools.partial(workflow_authority, "external_target")
            if workflow_authority
            else None
        ),
        validate_target_source=_workflow_target_validator(
            plan,
            root,
            generated_input_refs,
            authority=workflow_authority,
            _consumer_source_root=consumer_source_root,
        ),
    )
    source_current = binding_current()
    results = []
    any_failed = False
    for item in wanted:
        name = item.get("name")
        item_rule = rule_refs.get(name) if isinstance(rule_refs, dict) else rule
        entry = suite.get(name) or {
            "name": name,
            "selected": item.get("status"),
            "status": "not_executed",
            "reason": "workflow_script_check_missing",
            **({"rule_ref": item_rule} if item_rule is not None else {}),
        }
        entry = dict(entry)
        entry.setdefault("plan_authorized", True)
        if rule is not None and rule.get("path") and rule.get("sha256"):
            entry["config_input_ref"] = {"path": rule["path"], "sha256": rule["sha256"]}
        if not source_current and entry.get("execution_started"):
            entry["target_source_problem"] = (
                "workflow_target_plan_changed_during_execution"
            )
            if entry["status"] != "failed":
                entry.setdefault("native_status", entry["status"])
                entry.update(status="unknown", reason=entry["target_source_problem"])
        if entry.get("status") == "failed" or (
            entry.get("status") == "not_executed"
            and str(entry.get("reason") or "").startswith(
                ("workflow_config_", "workflow_target_")
            )
        ):
            any_failed = True
        results.append(entry)
    results.extend(omitted_entries)
    return results, any_failed


PRODUCER_HANDOFF_KIND = "engineering-ci-producer-artifact-handoff"
PRODUCER_HANDOFF_VERSION = 1

_EVENT_IDENTITY_FIELDS = (
    "snapshot",
    "execution_tree",
    "tree",
    "index_tree",
    "merge_tree",
    "head_commit",
    "base_commit",
    "base_tree",
    "remote_ref",
    "local_ref",
    "ref_action",
)


def _plan_event_identity(plan):
    git = (plan or {}).get("git") or {}
    return {key: git[key] for key in _EVENT_IDENTITY_FIELDS if key in git}


def _plan_candidate_identity(plan):
    git = (plan or {}).get("git") or {}
    binding = git.get("content_binding")
    digest = binding.get("digest") if isinstance(binding, dict) else None
    return {
        "snapshot": git.get("snapshot"),
        "execution_tree": git.get("execution_tree"),
        "tree": git.get("tree"),
        "index_tree": git.get("index_tree"),
        "merge_tree": git.get("merge_tree"),
        "head_commit": git.get("head_commit"),
        "source_tree_id": git.get("source_tree_id"),
        "content_binding_digest": digest,
    }


def _workflow_source_authorizer(plan, verify):
    """Bind Workflow/Shell Source Handoff to the original Plan and Git event."""
    if not callable(verify):
        return None
    plan_ref = _canonical_sha256(plan)
    event = _plan_event_identity(plan)

    def authorize(kind, check_name, claim):
        return (
            verify(
                {
                    "kind": kind,
                    "plan_ref": plan_ref,
                    "event": dict(event),
                    "check": check_name,
                    "source": dict(claim),
                }
            )
            is True
        )

    return authorize


def _workflow_tree_materialization(git, root, tree, check_name, authority):
    content_id = git.get("source_tree_id")
    if (
        authority is not None
        and _is_git_oid(tree)
        and _is_hex64(content_id)
        and authority(
            "tree_materialization",
            check_name,
            {"tree": tree, "source_tree_id": content_id},
        )
        and selection.directory_content_id(root) == content_id
    ):
        return content_id
    return None


def _workflow_target_validator(
    plan,
    root,
    generated_inputs,
    *,
    authority=None,
    _consumer_source_root=None,
):
    """Verify the observed target against its original Git or Producer source."""
    git = plan["git"]
    declared = (git.get("content_binding") or {}).get("paths") or {}
    tree = (
        git.get("execution_tree")
        or git.get("tree")
        or git.get("index_tree")
        or git.get("merge_tree")
    )
    immutable = git.get("snapshot") in {"commit", "push", "pr_merge", "index"}
    base_root = _consumer_source_root if _consumer_source_root is not None else root
    git_available = immutable and selection._resolve_git_dir(base_root) is not None
    generated = {item["path"]: item for item in generated_inputs}
    verified_directories = {}
    materializations = {}

    def materialization_verified(check_name):
        if check_name not in materializations:
            materializations[check_name] = _workflow_tree_materialization(
                git, base_root, tree, check_name, authority
            )
        return materializations[check_name]

    def validate(target, *, check_name=None):
        if immutable and (
            not _is_git_oid(tree)
            or (not git_available and not materialization_verified(check_name))
        ):
            return "workflow_target_tree_unverified"
        path, resolved, digest = (
            target["path"],
            target["resolved_path"],
            target["sha256"],
        )
        for name, item in generated.items():
            if item["input_kind"] == "file" and name == path:
                if path != resolved or digest != item["sha256"]:
                    return "workflow_target_generated_input_mismatch"
                return None
            if item["input_kind"] == "directory" and path.startswith(name + "/"):
                if not resolved.startswith(name + "/"):
                    return "workflow_target_generated_input_mismatch"
                if name not in verified_directories:
                    verified_directories[name] = verify_artifact_output(
                        root,
                        {"kind": "directory", "path": name, "sha256": item["sha256"]},
                    )["ok"]
                if not verified_directories[name]:
                    return "workflow_target_generated_input_mismatch"
                return None
        if target["policy_scope"] == "event_external":
            return None  # The runtime authority and exact grant already verified it.
        if path in declared and declared[path] != digest:
            return "workflow_target_plan_digest_mismatch"
        if git_available:
            blob = selection._git_bytes(
                base_root, "cat-file", "blob", f"{tree}:{resolved}"
            )
            if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != digest:
                return "workflow_target_tree_mismatch"
            # Reuse the Git binding check for every component of an alias.
            if (
                path != resolved
                and not _verify_git_execution_binding(plan, base_root)[0]
            ):
                return "workflow_target_tree_mismatch"
        return None

    return validate


def _prerequisite_actions(plan, override):
    if override is not None:
        return dict(override)
    actions = plan.get("prerequisite_actions")
    if isinstance(actions, dict):
        return dict(actions)
    inputs = plan.get("inputs") or {}
    declared = inputs.get("prerequisite_actions") if isinstance(inputs, dict) else None
    return dict(declared) if isinstance(declared, dict) else {}


def _run_command(argv, root, *, runner=None, artifact_root=None):
    rendered = [
        arg.replace("{artifact_dir}", str(artifact_root))
        if artifact_root is not None
        else arg
        for arg in argv
    ]
    if runner is not None:
        try:
            return runner(list(rendered), root), rendered
        except TypeError:
            return runner(list(rendered), root, None), rendered
    if artifact_root is None:
        return hygiene._run(rendered, root), rendered
    Path(artifact_root).mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ENGINEERING_CI_ARTIFACT_DIR"] = str(artifact_root)
    try:
        completed = subprocess.run(
            [str(item) for item in rendered],
            cwd=root,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        completed = subprocess.CompletedProcess(
            rendered, 127, "", f"missing executable: {rendered[0]}"
        )
    except OSError as error:
        completed = subprocess.CompletedProcess(
            rendered, 126, "", f"command execution failed: {error}"
        )
    return completed, rendered


def verify_artifact_output(artifact_root, output_ref):
    """Verify isolated derived File/Directory artifact existence and digest."""
    kind = output_ref["kind"]
    rel = output_ref["path"]
    expected = output_ref.get("sha256")
    base_result = {
        "kind": kind,
        "path": rel,
        "expected_sha256": expected,
        "sha256": None,
        "files": [],
        "file_digests": {},
        "materialized_objects": [],
    }
    if artifact_root is None:
        return {**base_result, "ok": False, "reason": "data_artifact_missing"}
    root_path = Path(artifact_root).resolve()
    target = (root_path / rel).resolve()
    if not target.is_relative_to(root_path):
        return {**base_result, "ok": False, "reason": "data_artifact_path_invalid"}
    cursor = root_path
    for part in Path(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return {**base_result, "ok": False, "reason": "data_artifact_symlink"}
    if not target.exists():
        return {**base_result, "ok": False, "reason": "data_artifact_missing"}
    if kind == "file":
        if not target.is_file():
            return {**base_result, "ok": False, "reason": "data_artifact_type_mismatch"}
        digest = check_contract.file_digest(target)
        files = [Path(rel).as_posix()]
        file_digests = {files[0]: digest}
        objects = [{"kind": "file", "path": rel}]
    else:
        if not target.is_dir():
            return {**base_result, "ok": False, "reason": "data_artifact_type_mismatch"}
        files = []
        file_digests = {}
        for entry in sorted(
            target.rglob("*"), key=lambda item: item.relative_to(target).as_posix()
        ):
            if entry.is_symlink():
                return {**base_result, "ok": False, "reason": "data_artifact_symlink"}
            if entry.is_file():
                rel_entry = entry.relative_to(root_path).as_posix()
                files.append(rel_entry)
                file_digests[rel_entry] = check_contract.file_digest(entry)
        digest = artifact_directory_id(target)
        objects = [{"kind": "directory", "path": rel}]
    if expected is not None and digest != expected:
        return {
            **base_result,
            "ok": False,
            "reason": "data_artifact_digest_mismatch",
            "sha256": digest,
            "files": files,
            "file_digests": file_digests,
        }
    return {
        **base_result,
        "ok": True,
        "reason": None,
        "sha256": digest,
        "files": files,
        "file_digests": file_digests,
        "materialized_objects": objects,
    }


def edge_node_key(ref):
    kind = ref.get("kind")
    if kind == "tests":
        return "tests"
    return (kind, ref.get("name"))


def pre_execution_edges(plan):
    contract = plan.get("dependency_contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != 2:
        return {}
    grouped = {}
    for edge in contract.get("edges") or ():
        if edge.get("execution_phase") == "pre_execution":
            grouped.setdefault(edge_node_key(edge["consumer_ref"]), []).append(edge)
    return grouped


def artifact_target_paths(files):
    """Expand delivered Files/Directories into Native Docs target paths."""
    paths = []
    for producer_root, rel, kind in files:
        if kind == "directory":
            directory = Path(producer_root) / rel
            paths.extend(
                entry.relative_to(producer_root).as_posix()
                for entry in directory.rglob("*")
                if entry.is_file()
            )
        else:
            paths.append(rel)
    return sorted(dict.fromkeys(paths))


def _artifact_paths_overlap(first, second):
    a, b = first.rstrip("/"), second.rstrip("/")
    if os.name == "nt":
        a, b = a.casefold(), b.casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def artifact_directory_id(root):
    """Artifact-local Directory identity including empty directory nodes."""
    root = Path(root)
    digest = hashlib.sha256()
    digest.update(b"eci-artifact-directory-v1\0")
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_dir():
            digest.update(b"D")
            digest.update(len(rel).to_bytes(8, "big"))
            digest.update(rel)
        elif path.is_file():
            content = path.read_bytes()
            digest.update(b"F")
            digest.update(len(rel).to_bytes(8, "big"))
            digest.update(rel)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()


def producer_artifact_root(artifact_root, producer_ref):
    """Isolated artifact namespace for one declared Producer."""
    kind = producer_ref.get("kind")
    name = producer_ref.get("name")
    if kind not in {"prerequisite_action", "check"}:
        raise ValueError("data_producer_kind_unsupported")
    if (
        not isinstance(name, str)
        or name in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
    ):
        raise ValueError("data_producer_name_invalid")
    root = (Path(artifact_root) / kind / name).resolve()
    if not root.is_relative_to(Path(artifact_root).resolve()):
        raise ValueError("data_producer_name_invalid")
    return root


def build_consumer_view(source_root, artifacts):
    """Copy source into an isolated view and overlay verified producer files."""
    temporary = tempfile.TemporaryDirectory(prefix="eci-consumer-")
    source_root = Path(source_root).resolve()
    view = Path(temporary.name).resolve() / "source"

    def skip_junctions(directory, names):
        return [name for name in names if (Path(directory) / name).is_junction()]

    try:
        shutil.copytree(source_root, view, symlinks=True, ignore=skip_junctions)
        for directory, dirs, files in os.walk(view, followlinks=False):
            for name in (*dirs, *files):
                link = Path(directory) / name
                if not link.is_symlink():
                    continue
                target = link.readlink()
                if not target.is_absolute():
                    continue
                target = Path(os.path.normpath(target))
                if target.is_relative_to(source_root):
                    mapped = view / target.relative_to(source_root)
                    link.unlink()
                    link.symlink_to(
                        os.path.relpath(mapped, link.parent),
                        target_is_directory=name in dirs,
                    )
    except OSError as error:
        temporary.cleanup()
        raise ExecutionError("data_consumer_view_unavailable") from error
    for item in artifacts:
        if len(item) == 3:
            producer_root, rel, kind = item
        else:
            producer_root, rel = item
            kind = "file"
        target = view / rel
        source = Path(producer_root) / rel
        destinations = [target]
        if kind == "directory":
            destinations.extend(
                target / path.relative_to(source) for path in source.rglob("*")
            )
        if any(
            not path.is_relative_to(view) or path.resolve() != path
            for path in destinations
        ):
            temporary.cleanup()
            raise ExecutionError("data_artifact_target_symlink")
        if kind == "directory":
            shutil.copytree(
                source,
                target,
                dirs_exist_ok=True,
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return temporary, view


def generated_input_refs(closures, consumer_ref):
    """Derive Native Result inputs from verified producer closures only."""
    refs = []
    for closure in closures:
        if closure.get("consumer_ref") != consumer_ref:
            continue
        if not closure.get("verified"):
            continue
        output = closure.get("output_ref") or {}
        kind = output.get("kind")
        path = output.get("path")
        if kind not in {"file", "directory"} or not isinstance(path, str):
            continue
        refs.append(
            {
                "kind": "generated_input",
                "input_kind": kind,
                "producer_ref": closure["producer_ref"],
                "path": path,
                "sha256": closure.get("sha256"),
            }
        )
    return refs


def evaluate_edge(edge, producer_result, *, artifact_root=None):
    """Evaluate PASS and derived-artifact closure for one pre-execution edge."""
    if edge.get("resolution") == "unresolved":
        return False, "prerequisite_unresolved", None
    status = (producer_result or {}).get("status")
    if edge.get("requires_pass") and status != "passed":
        reason = (
            "pre_execution_prerequisite_not_executed"
            if status == "not_executed"
            else "pre_execution_prerequisite_failed"
        )
        return False, reason, None
    if edge["dependency_kind"] == "data":
        closure = verify_artifact_output(artifact_root, edge["output_ref"])
        entry = {
            "producer_ref": edge["producer_ref"],
            "consumer_ref": edge["consumer_ref"],
            "output_ref": edge["output_ref"],
            "verified": closure["ok"],
            "reason": closure["reason"],
            "sha256": closure["sha256"],
            "materialized_files": closure["files"],
            "materialized_file_digests": dict(closure.get("file_digests") or {}),
            "materialized_objects": closure.get("materialized_objects", []),
        }
        if not closure["ok"]:
            return False, closure["reason"], entry
        return True, None, entry
    return True, None, None


PRODUCER_HANDOFF_UNVERIFIED_REASONS = frozenset(
    {
        "source_resolution_generated_inputs_unproven",
        "source_resolution_missing",
        "source_resolution_root_missing",
        "source_resolution_profile_unverified",
        "source_resolution_baseline_unproven",
        "source_resolution_scope_required",
        "producer_handoff_invalid",
        "producer_handoff_version_unsupported",
        "producer_handoff_missing",
        "producer_handoff_attempt_unverified",
        "producer_execution_not_verified",
        "producer_artifact_authority_unverified",
        "producer_artifact_missing",
        "producer_artifact_unreadable",
        "producer_artifact_expired",
        "workflow_config_unverified",
        "workflow_target_tree_unverified",
        "workflow_external_source_unverified",
        "workflow_external_config_unverified",
        "source_config_unreadable",
        "hygiene_config_unreadable",
        "selection_config_unreadable",
    }
)


def _classify_resolution_error(code):
    """Distinguish missing/unverified proof from proved conflict/tampering."""
    if code in PRODUCER_HANDOFF_UNVERIFIED_REASONS:
        return "unverified"
    return "contradicted"


def _edge_retention_key(producer_ref, consumer_ref, output_ref):
    p_ref = producer_ref or {}
    c_ref = consumer_ref or {}
    o_ref = output_ref or {}
    return (
        str(p_ref.get("kind") or ""),
        str(p_ref.get("name") or ""),
        str(c_ref.get("kind") or ""),
        str(c_ref.get("name") or ""),
        str(o_ref.get("kind") or ""),
        str(o_ref.get("path") or ""),
        str(o_ref.get("sha256") or ""),
    )


def _normalize_rel_artifact_path(rel_path):
    if not isinstance(rel_path, str) or not rel_path:
        raise ExecutionError("producer_artifact_path_invalid")
    if (
        rel_path.startswith("/")
        or "\\" in rel_path
        or re.match(r"^[A-Za-z]:", rel_path)
    ):
        raise ExecutionError("producer_artifact_path_invalid")
    parts = rel_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ExecutionError("producer_artifact_path_invalid")
    return "/".join(parts)


def _assert_no_symlink_components(
    base_root, target_path, *, error_code="producer_artifact_symlink"
):
    base = Path(base_root)
    rel = target_path.relative_to(base)
    current = base
    for part in rel.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ExecutionError(error_code)


def retained_producer_root(bundle_root, producer_ref):
    """Confined retained artifact namespace under ``<bundle>/produced/<kind>/<name>``."""
    base = Path(bundle_root) / "produced"
    kind = (producer_ref or {}).get("kind")
    name = (producer_ref or {}).get("name")
    if kind not in {"prerequisite_action", "check"}:
        raise ExecutionError("producer_artifact_path_invalid")
    if (
        not isinstance(name, str)
        or name in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
    ):
        raise ExecutionError("producer_artifact_path_invalid")
    if base.exists() and (base.is_symlink() or base.is_junction()):
        raise ExecutionError("producer_artifact_symlink")
    target = base / kind / name
    _assert_no_symlink_components(Path(bundle_root), target)
    return target.resolve()


def _resolve_confined_artifact_path(root_path, rel_path):
    normalized = _normalize_rel_artifact_path(rel_path)
    root = Path(root_path).resolve()
    target = root / normalized
    try:
        resolved = target.resolve()
    except OSError as error:
        raise ExecutionError("producer_artifact_unreadable") from error
    if not resolved.is_relative_to(root):
        raise ExecutionError("producer_artifact_path_invalid")
    _assert_no_symlink_components(root, target)
    return normalized, target


def _inspect_artifact_entries(root_path, rel_path, expected_kind):
    normalized, target = _resolve_confined_artifact_path(root_path, rel_path)
    root = Path(root_path).resolve()
    if not target.exists():
        raise ExecutionError("producer_artifact_missing")
    if target.is_symlink() or target.is_junction():
        raise ExecutionError("producer_artifact_symlink")
    if expected_kind == "file":
        if not target.is_file():
            raise ExecutionError("producer_artifact_type_mismatch")
        try:
            content = target.read_bytes()
        except OSError as error:
            raise ExecutionError("producer_artifact_unreadable") from error
        sha = hashlib.sha256(content).hexdigest()
        return {
            "kind": "file",
            "sha256": sha,
            "materialized_files": [normalized],
            "materialized_dirs": [],
            "file_digests": {normalized: sha},
        }
    if expected_kind == "directory":
        if not target.is_dir():
            raise ExecutionError("producer_artifact_type_mismatch")
        dirs = [normalized]
        files = []
        file_digests = {}
        try:
            entries = sorted(
                target.rglob("*"), key=lambda item: item.relative_to(target).as_posix()
            )
        except OSError as error:
            raise ExecutionError("producer_artifact_unreadable") from error
        for entry in entries:
            if entry.is_symlink() or entry.is_junction():
                raise ExecutionError("producer_artifact_symlink")
            rel_entry = entry.relative_to(root).as_posix()
            if entry.is_dir():
                dirs.append(rel_entry)
            elif entry.is_file():
                try:
                    raw = entry.read_bytes()
                except OSError as error:
                    raise ExecutionError("producer_artifact_unreadable") from error
                files.append(rel_entry)
                file_digests[rel_entry] = hashlib.sha256(raw).hexdigest()
        return {
            "kind": "directory",
            "sha256": artifact_directory_id(target),
            "materialized_files": files,
            "materialized_dirs": dirs,
            "file_digests": file_digests,
        }
    raise ExecutionError("producer_artifact_type_mismatch")


def _artifact_retention_snapshot(producer_root, output_ref, *, producer_ref):
    rel = output_ref.get("path")
    kind = output_ref.get("kind")
    snap = _inspect_artifact_entries(producer_root, rel, kind)
    normalized = _normalize_rel_artifact_path(rel)
    bundle_path = f"produced/{producer_ref['kind']}/{producer_ref['name']}/{normalized}"
    return {
        **snap,
        "bundle_path": bundle_path,
        "retention": {
            "status": "retained",
            "layout": "produced/<kind>/<name>/<rel>",
            "valid": True,
        },
    }


def retain_closure_artifacts(artifact_root, bundle_root, closure_records):
    """Copy verified Data-edge outputs to retained storage and re-verify bytes."""
    retained = {}
    bundle_path = Path(bundle_root)
    for rec in closure_records or ():
        if not rec.get("verified"):
            continue
        p_ref = rec["producer_ref"]
        o_ref = rec["output_ref"]
        rel = o_ref["path"]
        kind = o_ref["kind"]
        key = _edge_retention_key(p_ref, rec.get("consumer_ref"), o_ref)
        src_root = producer_artifact_root(artifact_root, p_ref)
        dst_root = retained_producer_root(bundle_path, p_ref)
        _, src_target = _resolve_confined_artifact_path(src_root, rel)
        snap_before = _inspect_artifact_entries(src_root, rel, kind)
        norm_rel = _normalize_rel_artifact_path(rel)
        dst_target = dst_root / norm_rel
        dst_target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "directory":
            shutil.copytree(src_target, dst_target, symlinks=False, dirs_exist_ok=True)
        else:
            shutil.copy2(src_target, dst_target)
        post = verify_artifact_output(dst_root, o_ref)
        if not post.get("ok") or post.get("sha256") != rec.get("sha256"):
            raise ExecutionError(
                post.get("reason") or "producer_artifact_digest_mismatch"
            )
        snap_after = _artifact_retention_snapshot(dst_root, o_ref, producer_ref=p_ref)
        if snap_after["sha256"] != snap_before["sha256"] or (
            snap_after["file_digests"] != snap_before["file_digests"]
        ):
            raise ExecutionError("producer_artifact_digest_mismatch")
        retained[key] = snap_after
    return retained


def _producer_execution_summary(
    producer_ref, result, *, execution_identity=None, execution_context=None
):
    exec_ref = (
        execution_identity.get("execution_ref")
        if isinstance(execution_identity, dict)
        else None
    )
    if not isinstance(result, dict):
        return {
            "kind": producer_ref.get("kind"),
            "name": producer_ref.get("name"),
            "status": "not_executed",
            "reason": "producer_execution_not_verified",
            "execution_context": execution_context,
            "execution_ref": exec_ref,
            "prerequisite_result_sha256": None,
        }
    p_type = result.get("type")
    result_origin = result.get("result_origin")
    if result_origin is None and p_type == "command":
        result_origin = "host_subprocess"
    summary = {
        "kind": producer_ref.get("kind"),
        "name": producer_ref.get("name"),
        "type": p_type,
        "status": result.get("status", "not_executed"),
        "exit_code": result.get("exit_code"),
        "reason": result.get("reason"),
        "execution_context": result.get("execution_context") or execution_context,
        "result_origin": result_origin,
        "execution_ref": exec_ref,
        "prerequisite_result_sha256": _canonical_sha256(result),
    }
    if isinstance(result.get("artifact_ref"), dict):
        summary["artifact_ref"] = dict(result["artifact_ref"])
    return summary


def build_producer_artifact_handoff(
    plan,
    *,
    closure_records,
    prerequisite_results=(),
    execution_identity=None,
    execution_context="host",
    retention_metadata=None,
):
    """Build the self-consistent #86 Producer Artifact Handoff over retained outputs."""
    prereq_by_name = {
        item.get("name"): item
        for item in prerequisite_results or ()
        if isinstance(item, dict) and item.get("name")
    }
    retention_map = retention_metadata if isinstance(retention_metadata, dict) else {}
    artifacts = []
    for rec in closure_records or ():
        if not rec.get("verified"):
            continue
        p_ref = rec["producer_ref"]
        c_ref = rec["consumer_ref"]
        o_ref = rec["output_ref"]
        key = _edge_retention_key(p_ref, c_ref, o_ref)
        meta = retention_map.get(key)
        if not isinstance(meta, dict):
            continue
        p_exec = _producer_execution_summary(
            p_ref,
            prereq_by_name.get(p_ref.get("name")),
            execution_identity=execution_identity,
            execution_context=execution_context,
        )
        artifacts.append(
            {
                "producer_ref": dict(p_ref),
                "producer_execution": p_exec,
                "closure_sha256": _canonical_sha256(rec),
                "consumer_ref": dict(c_ref),
                "output_ref": dict(o_ref),
                "kind": o_ref.get("kind"),
                "path": o_ref.get("path"),
                "sha256": meta.get("sha256") or rec.get("sha256"),
                "materialized_files": list(
                    meta.get("materialized_files")
                    or rec.get("materialized_files")
                    or ()
                ),
                "materialized_dirs": list(meta.get("materialized_dirs") or ()),
                "file_digests": dict(meta.get("file_digests") or {}),
                "bundle_path": meta["bundle_path"],
                "retention": dict(
                    meta.get("retention")
                    or {
                        "status": "retained",
                        "layout": "produced/<kind>/<name>/<rel>",
                        "valid": True,
                    }
                ),
            }
        )
    artifacts.sort(
        key=lambda item: _edge_retention_key(
            item["producer_ref"], item["consumer_ref"], item["output_ref"]
        )
    )
    producer_executions = [
        _producer_execution_summary(
            {"kind": "prerequisite_action", "name": name},
            prereq_by_name[name],
            execution_identity=execution_identity,
            execution_context=execution_context,
        )
        for name in sorted(prereq_by_name)
    ]
    payload = {
        "schema_version": PRODUCER_HANDOFF_VERSION,
        "kind": PRODUCER_HANDOFF_KIND,
        "plan_ref": _canonical_sha256(plan),
        "event": _plan_event_identity(plan),
        "candidate": _plan_candidate_identity(plan),
        "execution_context": execution_context,
        "execution_identity": dict(execution_identity)
        if isinstance(execution_identity, dict)
        else None,
        "layout": "produced/<kind>/<name>/<rel>",
        "producer_executions": producer_executions,
        "artifacts": artifacts,
    }
    return {**payload, "handoff_digest": _canonical_sha256(payload)}


def _is_prefixed_sha256(value):
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _is_hex64(value.removeprefix("sha256:"))
    )


def _resolve_expected_run_attempt(
    expected_run_ref, expected_attempt_ref, execution_context_identity
):
    ctx_run = None
    ctx_attempt = None
    if execution_context_identity is not None:
        if not isinstance(execution_context_identity, dict):
            raise ExecutionError("producer_handoff_attempt_unverified")
        ctx_run = execution_context_identity.get("run_ref") or None
        ctx_attempt = execution_context_identity.get("attempt_ref") or None
        if bool(ctx_run) != bool(ctx_attempt):
            raise ExecutionError("producer_handoff_attempt_unverified")
    if bool(expected_run_ref) != bool(expected_attempt_ref):
        raise ExecutionError("producer_handoff_attempt_unverified")
    if expected_run_ref and ctx_run and expected_run_ref != ctx_run:
        raise ExecutionError("producer_handoff_attempt_mismatch")
    if expected_attempt_ref and ctx_attempt and expected_attempt_ref != ctx_attempt:
        raise ExecutionError("producer_handoff_attempt_mismatch")
    return (expected_run_ref or ctx_run or None), (
        expected_attempt_ref or ctx_attempt or None
    )


def producer_artifact_authority_from_execution(
    plan,
    report,
    *,
    bundle_root,
    allowed_checks=None,
    expected_run_ref=None,
    expected_attempt_ref=None,
):
    """Build a runtime ``producer_artifact`` authority bound to original Execution facts.

    Uses only trusted Plan, original Prerequisite Results, Artifact Closure,
    Execution Identity, and retained bundle root; never reads ``report['checks']``
    or NativeResult self-reported inputs.
    """
    plan_ref = _canonical_sha256(plan)
    expected_event = _plan_event_identity(plan)
    expected_candidate = _plan_candidate_identity(plan)
    resolved_bundle = Path(bundle_root).resolve()
    prereq_digests = {}
    for item in (report or {}).get("prerequisites") or ():
        if (
            isinstance(item, dict)
            and item.get("name")
            and item.get("status") == "passed"
            and type(item.get("exit_code")) is int
            and item.get("exit_code") == 0
            and item.get("reason") is None
        ):
            prereq_digests[item["name"]] = _canonical_sha256(item)
    closure_digests = {}
    for rec in (report or {}).get("artifact_closure") or ():
        if isinstance(rec, dict) and rec.get("verified"):
            key = _edge_retention_key(
                rec.get("producer_ref"), rec.get("consumer_ref"), rec.get("output_ref")
            )
            closure_digests[key] = _canonical_sha256(rec)
    trusted_handoff_id = ((report or {}).get("producer_artifact_handoff") or {}).get(
        "handoff_digest"
    )
    trusted_exec_ident = (report or {}).get("execution_identity")
    trusted_exec_context = (report or {}).get("execution_context")
    plan_git = (plan or {}).get("git") or {}
    expected_tree = (
        plan_git.get("execution_tree")
        or plan_git.get("tree")
        or plan_git.get("index_tree")
        or plan_git.get("merge_tree")
    )
    expected_tree_id = plan_git.get("source_tree_id")
    tree_materialization_verified = (
        (report or {}).get("source_tree_verified") == "verified"
        and (report or {}).get("tree_verified") is True
        and bool(expected_tree)
        and bool(expected_tree_id)
        and (report or {}).get("source_tree_id") == expected_tree_id
    )

    def authorize(request):
        if (
            request.get("plan_ref") != plan_ref
            or request.get("event") != expected_event
        ):
            return False
        if allowed_checks is not None and request.get("check") not in allowed_checks:
            return False
        src = request.get("source")
        if not isinstance(src, dict):
            return False
        if request.get("kind") == "tree_materialization":
            return tree_materialization_verified and src == {
                "tree": expected_tree,
                "source_tree_id": expected_tree_id,
            }
        if request.get("kind") != "producer_artifact":
            return False
        if not trusted_handoff_id or src.get("handoff_id") != trusted_handoff_id:
            return False
        if src.get("candidate") != expected_candidate:
            return False
        if src.get("execution_identity") != trusted_exec_ident:
            return False
        if (
            trusted_exec_context is not None
            and src.get("execution_context") != trusted_exec_context
        ):
            return False
        p_ref = src.get("producer_ref") or {}
        p_name = p_ref.get("name")
        if not p_name or src.get("prerequisite_result_sha256") != prereq_digests.get(
            p_name
        ):
            return False
        edge_key = _edge_retention_key(
            p_ref, src.get("consumer_ref"), src.get("output_ref")
        )
        if src.get("closure_sha256") != closure_digests.get(edge_key):
            return False
        if (expected_run_ref is not None or expected_attempt_ref is not None) and (
            src.get("expected_run_ref") != expected_run_ref
            or src.get("expected_attempt_ref") != expected_attempt_ref
        ):
            return False
        try:
            return (
                Path(src["resolved_bundle_root"])
                .resolve()
                .is_relative_to(resolved_bundle)
            )
        except (KeyError, OSError, TypeError, ValueError):
            return False

    return authorize


def _validate_handoff_envelope(
    plan,
    handoff,
    *,
    expected_run_ref=None,
    expected_attempt_ref=None,
    execution_context_identity=None,
):
    if not isinstance(handoff, dict):
        raise ExecutionError("producer_handoff_invalid")
    if handoff.get("kind") != PRODUCER_HANDOFF_KIND:
        raise ExecutionError("producer_handoff_invalid")
    if handoff.get("schema_version") != PRODUCER_HANDOFF_VERSION:
        raise ExecutionError("producer_handoff_version_unsupported")
    recorded_digest = handoff.get("handoff_digest")
    if not isinstance(recorded_digest, str) or not recorded_digest:
        raise ExecutionError("producer_handoff_invalid")
    body = {k: v for k, v in handoff.items() if k != "handoff_digest"}
    if _canonical_sha256(body) != recorded_digest:
        raise ExecutionError("producer_handoff_digest_mismatch")
    if handoff.get("event") != _plan_event_identity(plan):
        raise ExecutionError("producer_handoff_event_mismatch")
    if handoff.get("candidate") != _plan_candidate_identity(plan):
        raise ExecutionError("producer_handoff_candidate_mismatch")
    if handoff.get("plan_ref") != _canonical_sha256(plan):
        raise ExecutionError("producer_handoff_plan_mismatch")
    exp_run, exp_attempt = _resolve_expected_run_attempt(
        expected_run_ref, expected_attempt_ref, execution_context_identity
    )
    ident = handoff.get("execution_identity")
    if ident is not None and not isinstance(ident, dict):
        raise ExecutionError("producer_handoff_invalid")
    h_run = ident.get("run_ref") if isinstance(ident, dict) else None
    h_attempt = ident.get("attempt_ref") if isinstance(ident, dict) else None
    if bool(h_run) != bool(h_attempt):
        raise ExecutionError("producer_handoff_attempt_unverified")
    if h_run or h_attempt:
        if not exp_run or not exp_attempt:
            raise ExecutionError("producer_handoff_attempt_unverified")
        if h_run != exp_run or h_attempt != exp_attempt:
            raise ExecutionError("producer_handoff_attempt_mismatch")
    elif exp_run or exp_attempt:
        raise ExecutionError("producer_handoff_attempt_unverified")
    if not isinstance(handoff.get("artifacts"), list):
        raise ExecutionError("producer_handoff_invalid")
    return body, exp_run, exp_attempt


def _match_handoff_record(edge, handoff):
    p_ref = edge["producer_ref"]
    c_ref = edge["consumer_ref"]
    o_ref = edge["output_ref"]
    artifacts = handoff.get("artifacts") or ()
    exact = next(
        (
            rec
            for rec in artifacts
            if isinstance(rec, dict)
            and rec.get("producer_ref") == p_ref
            and rec.get("consumer_ref") == c_ref
            and rec.get("output_ref") == o_ref
        ),
        None,
    )
    if exact is not None:
        return exact
    for rec in artifacts:
        if not isinstance(rec, dict):
            continue
        same_c = rec.get("consumer_ref") == c_ref
        same_p = rec.get("producer_ref") == p_ref
        same_o = rec.get("output_ref") == o_ref
        if same_c and same_o and not same_p:
            raise ExecutionError("producer_handoff_producer_mismatch")
        if same_p and same_o and not same_c:
            raise ExecutionError("producer_handoff_consumer_mismatch")
        if same_p and same_c and not same_o:
            raise ExecutionError("producer_handoff_output_ref_mismatch")
    for rec in artifacts:
        if not isinstance(rec, dict):
            continue
        rec_o = rec.get("output_ref") or {}
        if rec_o.get("path") == o_ref.get("path"):
            if rec.get("producer_ref") != p_ref:
                raise ExecutionError("producer_handoff_producer_mismatch")
            if rec.get("consumer_ref") != c_ref:
                raise ExecutionError("producer_handoff_consumer_mismatch")
            raise ExecutionError("producer_handoff_output_ref_mismatch")
    for p_exec in handoff.get("producer_executions") or ():
        if (
            isinstance(p_exec, dict)
            and p_exec.get("kind") == p_ref.get("kind")
            and p_exec.get("name") == p_ref.get("name")
        ):
            status = p_exec.get("status")
            if status == "failed":
                raise ExecutionError("producer_execution_failed")
            if status != "passed":
                raise ExecutionError("producer_execution_not_verified")
    raise ExecutionError("producer_handoff_missing")


def _validate_producer_execution_claim(plan, edge, record, handoff):
    p_exec = record.get("producer_execution")
    if not isinstance(p_exec, dict):
        raise ExecutionError("producer_execution_not_verified")
    if p_exec.get("kind") != edge["producer_ref"].get("kind") or p_exec.get(
        "name"
    ) != edge["producer_ref"].get("name"):
        raise ExecutionError("producer_handoff_producer_mismatch")
    p_status = p_exec.get("status")
    if p_status == "failed":
        raise ExecutionError("producer_execution_failed")
    if p_status != "passed":
        raise ExecutionError("producer_execution_not_verified")

    p_type = p_exec.get("type")
    declared = _prerequisite_actions(plan, None).get(edge["producer_ref"].get("name"))
    if isinstance(declared, dict) and declared.get("type"):
        if p_type != declared.get("type"):
            raise ExecutionError("producer_execution_inconsistent")
    elif p_type not in {"command", "dagger_command", "git_commit_present"}:
        raise ExecutionError("producer_execution_inconsistent")

    exit_code = p_exec.get("exit_code")
    if type(exit_code) is not int or exit_code != 0:
        raise ExecutionError("producer_execution_inconsistent")
    if p_exec.get("reason") is not None:
        raise ExecutionError("producer_execution_inconsistent")

    ident = handoff.get("execution_identity")
    if (
        isinstance(ident, dict)
        and ident.get("execution_ref")
        and p_exec.get("execution_ref") != ident.get("execution_ref")
    ):
        raise ExecutionError("producer_execution_inconsistent")
    h_ctx = handoff.get("execution_context")
    if h_ctx is not None and p_exec.get("execution_context") not in {None, h_ctx}:
        raise ExecutionError("producer_execution_inconsistent")

    prereq_sha = p_exec.get("prerequisite_result_sha256")
    if prereq_sha is None:
        raise ExecutionError("producer_execution_not_verified")
    if not _is_prefixed_sha256(prereq_sha):
        raise ExecutionError("producer_execution_inconsistent")

    closure_sha = record.get("closure_sha256")
    if closure_sha is None:
        raise ExecutionError("producer_execution_not_verified")
    if not _is_prefixed_sha256(closure_sha):
        raise ExecutionError("producer_execution_inconsistent")

    for entry in handoff.get("producer_executions") or ():
        if (
            isinstance(entry, dict)
            and entry.get("kind") == edge["producer_ref"].get("kind")
            and entry.get("name") == edge["producer_ref"].get("name")
            and entry != p_exec
        ):
            raise ExecutionError("producer_execution_inconsistent")
    return p_exec, prereq_sha, closure_sha


def _verify_retained_bundle_artifact(
    plan,
    record,
    edge,
    *,
    bundle_root,
    check_name,
    authority,
    handoff,
    expected_run_ref=None,
    expected_attempt_ref=None,
):
    p_exec, prereq_sha, closure_sha = _validate_producer_execution_claim(
        plan, edge, record, handoff
    )

    retention = record.get("retention")
    if not isinstance(retention, dict):
        raise ExecutionError("producer_artifact_retention_invalid")
    r_status = retention.get("status")
    if r_status == "expired":
        raise ExecutionError("producer_artifact_expired")
    if r_status != "retained" or retention.get("valid") is False:
        raise ExecutionError("producer_artifact_retention_invalid")

    o_ref = edge["output_ref"]
    kind = o_ref.get("kind")
    rel = o_ref.get("path")
    if record.get("kind") != kind:
        raise ExecutionError("producer_artifact_type_mismatch")
    if record.get("path") != rel:
        raise ExecutionError("producer_handoff_output_ref_mismatch")

    expected_sha = record.get("sha256")
    edge_sha = o_ref.get("sha256")
    if not isinstance(expected_sha, str) or not _is_hex64(expected_sha):
        raise ExecutionError("producer_handoff_invalid")
    if edge_sha is not None and expected_sha != edge_sha:
        raise ExecutionError("producer_handoff_output_ref_mismatch")

    norm_rel = _normalize_rel_artifact_path(rel)
    expected_bundle_path = f"produced/{edge['producer_ref']['kind']}/{edge['producer_ref']['name']}/{norm_rel}"
    if record.get("bundle_path") != expected_bundle_path:
        raise ExecutionError("producer_artifact_path_invalid")

    if authority is None:
        raise ExecutionError("producer_artifact_authority_unverified")

    bundle_path = Path(bundle_root)
    if not bundle_path.exists():
        raise ExecutionError("producer_artifact_missing")
    bundle_producer_root = retained_producer_root(bundle_path, edge["producer_ref"])
    claim = {
        "handoff_id": handoff.get("handoff_digest"),
        "producer_ref": record["producer_ref"],
        "producer_execution": dict(p_exec),
        "prerequisite_result_sha256": prereq_sha,
        "closure_sha256": closure_sha,
        "consumer_ref": record["consumer_ref"],
        "output_ref": o_ref,
        "kind": kind,
        "path": rel,
        "sha256": expected_sha,
        "bundle_path": expected_bundle_path,
        "resolved_bundle_root": str(bundle_producer_root),
        "candidate": handoff.get("candidate"),
        "execution_context": handoff.get("execution_context"),
        "execution_identity": handoff.get("execution_identity"),
        "expected_run_ref": expected_run_ref,
        "expected_attempt_ref": expected_attempt_ref,
    }
    if not authority("producer_artifact", check_name, claim):
        raise ExecutionError("producer_artifact_authority_unverified")

    actual = _inspect_artifact_entries(bundle_producer_root, rel, kind)
    exp_files = list(record.get("materialized_files") or ())
    exp_dirs = list(record.get("materialized_dirs") or ())
    exp_digests = record.get("file_digests")
    if kind == "directory" and (
        set(exp_files) - set(actual["materialized_files"])
        or set(exp_dirs) - set(actual["materialized_dirs"])
    ):
        raise ExecutionError("producer_directory_truncated")
    if isinstance(exp_digests, dict):
        for f_rel, f_sha in exp_digests.items():
            if f_rel not in actual["file_digests"]:
                raise ExecutionError(
                    "producer_directory_truncated"
                    if kind == "directory"
                    else "producer_artifact_missing"
                )
            if actual["file_digests"][f_rel] != f_sha:
                raise ExecutionError("producer_artifact_digest_mismatch")
        if set(actual["file_digests"]) != set(exp_digests):
            raise ExecutionError("producer_artifact_digest_mismatch")
    elif set(actual["materialized_files"]) != set(exp_files):
        raise ExecutionError("producer_artifact_digest_mismatch")

    checked = verify_artifact_output(bundle_producer_root, o_ref)
    if not checked.get("ok"):
        code_map = {
            "data_artifact_missing": "producer_artifact_missing",
            "data_artifact_type_mismatch": "producer_artifact_type_mismatch",
            "data_artifact_symlink": "producer_artifact_symlink",
            "data_artifact_digest_mismatch": "producer_artifact_digest_mismatch",
            "data_artifact_path_invalid": "producer_artifact_path_invalid",
        }
        raise ExecutionError(
            code_map.get(checked.get("reason"), "producer_artifact_digest_mismatch")
        )
    if actual["sha256"] != expected_sha or checked.get("sha256") != expected_sha:
        raise ExecutionError("producer_artifact_digest_mismatch")

    return {
        "producer_root": bundle_producer_root,
        "rel": norm_rel,
        "kind": kind,
        "closure": {
            "producer_ref": dict(edge["producer_ref"]),
            "consumer_ref": dict(edge["consumer_ref"]),
            "output_ref": dict(o_ref),
            "verified": True,
            "reason": None,
            "sha256": actual["sha256"],
            "materialized_files": actual["materialized_files"],
            "materialized_objects": [{"kind": kind, "path": norm_rel}],
        },
        "record": record,
    }


def verify_producer_artifact_handoff(
    plan,
    check_name,
    *,
    handoff,
    producer_artifacts_dir,
    authority=None,
    expected_run_ref=None,
    expected_attempt_ref=None,
    execution_context_identity=None,
):
    """Independently verify #86 retained File/Directory artifacts for one Consumer Check."""
    edges = [
        edge
        for edge in pre_execution_edges(plan).get(("check", check_name), ())
        if edge.get("dependency_kind") == "data"
    ]
    if not edges:
        return None
    if handoff is None or producer_artifacts_dir is None:
        raise ExecutionError("source_resolution_generated_inputs_unproven")
    _, resolved_run_ref, resolved_attempt_ref = _validate_handoff_envelope(
        plan,
        handoff,
        expected_run_ref=expected_run_ref,
        expected_attempt_ref=expected_attempt_ref,
        execution_context_identity=execution_context_identity,
    )
    closures = []
    artifact_tuples = []
    verified_records = []
    seen_paths = {}
    for edge in edges:
        record = _match_handoff_record(edge, handoff)
        verified = _verify_retained_bundle_artifact(
            plan,
            record,
            edge,
            bundle_root=producer_artifacts_dir,
            check_name=check_name,
            authority=authority,
            handoff=handoff,
            expected_run_ref=resolved_run_ref,
            expected_attempt_ref=resolved_attempt_ref,
        )
        p_root = str(verified["producer_root"])
        rel = verified["rel"]
        if rel in seen_paths and seen_paths[rel] != p_root:
            raise ExecutionError("data_artifact_conflict")
        if any(
            _artifact_paths_overlap(rel, existing) and owner != p_root
            for existing, owner in seen_paths.items()
        ):
            raise ExecutionError("data_artifact_conflict")
        seen_paths[rel] = p_root
        closures.append(verified["closure"])
        artifact_tuples.append((verified["producer_root"], rel, verified["kind"]))
        verified_records.append(verified["record"])

    consumer_ref = {"kind": "check", "name": check_name}
    gen_refs = generated_input_refs(closures, consumer_ref)
    return {
        "handoff_id": handoff["handoff_digest"],
        "execution_identity": handoff.get("execution_identity"),
        "closures": closures,
        "artifact_tuples": artifact_tuples,
        "generated_input_refs": gen_refs,
        "artifact_paths": artifact_target_paths(artifact_tuples),
        "verified_artifacts": verified_records,
    }


def run_prerequisite_action(
    name,
    spec,
    *,
    root,
    runner=None,
    execution_context="host",
    artifact_root=None,
):
    if not isinstance(spec, dict) or not spec.get("type"):
        return {
            "name": name,
            "status": "not_executed",
            "reason": "prerequisite_parameters_missing",
        }
    kind = spec.get("type")
    if kind == "dagger_command":
        return {
            "name": name,
            "type": kind,
            "status": "not_executed",
            "reason": "prerequisite_requires_dagger",
            "exit_code": None,
        }
    if kind == "git_commit_present":
        commit = spec.get("commit")
        if not isinstance(commit, str) or not commit:
            return {
                "name": name,
                "type": kind,
                "status": "not_executed",
                "reason": "prerequisite_parameters_missing",
            }
        # An exported Directory is not the original Git object database.
        # Do not query an enclosing repository or label missing context as
        # a genuinely absent Commit. #66 owns an authenticated handoff.
        odb = (
            selection._git(root, "rev-parse", "--show-toplevel")
            if execution_context == "host"
            else None
        )
        if (
            odb is None
            or odb.returncode != 0
            or Path(odb.stdout.strip()).resolve() != root.resolve()
        ):
            return {
                "name": name,
                "type": kind,
                "status": "not_executed",
                "reason": "git_odb_unavailable_or_unbound",
                "commit": commit,
                "execution_context": execution_context,
                "result_origin": "git_context_probe"
                if odb is not None
                else "dagger_export_boundary",
                "exit_code": odb.returncode if odb is not None else None,
                "stdout": odb.stdout if odb is not None else "",
                "stderr": odb.stderr if odb is not None else "",
            }
        probe = selection._git(root, "cat-file", "-e", f"{commit}^{{commit}}")
        if probe.returncode == 0:
            return {
                "name": name,
                "type": kind,
                "status": "passed",
                "commit": commit,
                "git_odb_root": odb.stdout.strip(),
                "execution_context": execution_context,
                "result_origin": "git_cat_file",
                "exit_code": probe.returncode,
                "stdout": probe.stdout,
                "stderr": probe.stderr,
            }
        return {
            "name": name,
            "type": kind,
            "status": "failed",
            "reason": "identity_commit_missing",
            "commit": commit,
            "git_odb_root": odb.stdout.strip(),
            "execution_context": execution_context,
            "result_origin": "git_cat_file",
            "exit_code": probe.returncode,
            "stdout": probe.stdout,
            "stderr": probe.stderr,
        }
    if kind == "command":
        argv = spec.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) for item in argv)
        ):
            return {
                "name": name,
                "type": kind,
                "status": "not_executed",
                "reason": "prerequisite_parameters_missing",
            }
        completed, rendered = _run_command(
            argv, root, runner=runner, artifact_root=artifact_root
        )
        if completed.returncode == 0:
            return {
                "name": name,
                "type": kind,
                "status": "passed",
                "exit_code": 0,
                "argv": list(rendered),
                "stdout": getattr(completed, "stdout", "") or "",
                "stderr": getattr(completed, "stderr", "") or "",
            }
        return {
            "name": name,
            "type": kind,
            "status": "failed",
            "reason": "prerequisite_command_failed",
            "exit_code": completed.returncode,
            "argv": list(rendered),
            "stdout": getattr(completed, "stdout", "") or "",
            "stderr": getattr(completed, "stderr", "") or "",
        }
    return {
        "name": name,
        "type": kind,
        "status": "not_executed",
        "reason": "prerequisite_type_unsupported",
    }


def _run_prerequisites(
    names, actions, *, root, runner, execution_context="host", artifact_root=None
):
    results = []
    failed = False
    for name in names:
        item = run_prerequisite_action(
            name,
            actions.get(name),
            root=root,
            runner=runner,
            execution_context=execution_context,
            artifact_root=artifact_root,
        )
        if item.get("status") == "failed":
            failed = True
        results.append(item)
    return results, failed


def _source_reference(plan, execution_context):
    git = plan["git"]
    binding = git.get("content_binding")
    scope = []
    content_digest = None
    if isinstance(binding, dict):
        paths = binding.get("paths")
        if isinstance(paths, dict):
            scope = sorted(str(path) for path in paths)
        if not binding.get("unverified_paths"):
            content_digest = binding.get("digest")
    elif isinstance(git.get("changed_paths"), (list, tuple)):
        scope = sorted(
            str(path) for path in git["changed_paths"] if isinstance(path, str) and path
        )
    declared = git.get("execution_tree") or git.get("tree") or git.get("index_tree")
    if content_digest and scope:
        status = "content_digest"
    elif declared or git.get("head_commit") or git.get("index_tree"):
        status = "identity_only"
    else:
        status = "unknown"
    actual = "worktree" if execution_context == "host" else "dagger_directory"
    return check_contract.source_ref(
        str(git.get("snapshot") or "worktree"),
        declared_target_tree=declared,
        actual_execution_source=actual,
        head_commit=git.get("head_commit"),
        index_tree=git.get("index_tree"),
        content_digest=content_digest,
        verified_scope=scope,
        binding_status=status,
    )


def _baseline_reference(plan):
    git = plan["git"]
    if check_contract.is_unborn_index_baseline(git):
        return {"base_tree": git["base_tree"], "baseline_status": "unborn"}
    # Relative names alone are not immutable comparison inputs.
    if not git.get("base_commit"):
        return None
    ref = {"base_commit": git["base_commit"]}
    for key in ("base_ref", "base_tip"):
        if git.get(key) is not None:
            ref[key] = git[key]
    return ref


def _profile_reference(plan, *, root=None, execution_context=None, profile_ref=None):
    """Return the bound EffectiveProfile identity only when backed by verified Plan & source proof."""
    if not isinstance(plan, dict):
        return None
    inputs = plan.get("inputs") or {}
    prof_input = inputs.get("effective_profile") if isinstance(inputs, dict) else None
    gen_sha = (plan.get("generator") or {}).get("effective_profile_sha256")
    cfg_decl = inputs.get("config") if isinstance(inputs, dict) else None
    if not isinstance(cfg_decl, dict) or not isinstance(
        cfg_decl.get("normalized"), dict
    ):
        return None
    expected_block = plan.get("expected_checks")
    is_dagger = execution_context == "dagger_export"
    try:
        if root is not None:
            if is_dagger:
                source_tree_id = (plan.get("git") or {}).get("source_tree_id")
                if isinstance(expected_block, dict):
                    if not isinstance(source_tree_id, str) or not source_tree_id:
                        return None
                    if selection.directory_content_id(root) != source_tree_id:
                        return None
                require_file = cfg_decl.get("policy_scope") == "repo_target_tree"
                sel_cfg = selection.verify_config_identity_matches_file(
                    cfg_decl, root=root, require_file=require_file
                )
            else:
                sel_cfg = selection.verify_config_identity_matches_file(
                    cfg_decl, root=root, require_file=True
                )
        else:
            sel_cfg = selection.config_from_identity(cfg_decl)
    except (OSError, selection.SelectionError):
        return None

    if (
        not isinstance(prof_input, dict)
        or prof_input.get("authority_verified") is not True
    ):
        return None
    try:
        ep_policy = expected_checks._policy_approval(
            sel_cfg.effective_profile_policy, prof_input.get("approval_digest")
        )
    except expected_checks.ExpectedError:
        return None
    if not isinstance(ep_policy, dict) or not ep_policy:
        return None
    try:
        policy_pref = check_contract.profile_ref(
            (ep_policy.get("profile_ref") or {}).get("name"),
            (ep_policy.get("profile_ref") or {}).get("version"),
        )
    except (AttributeError, check_contract.CheckContractError):
        return None

    prof_sha = prof_input.get("profile_sha256")
    if (
        not isinstance(prof_sha, str)
        or not prof_sha
        or (prof_input.get("effective_profile_ref") or {}).get("sha256") != prof_sha
        or prof_input.get("profile_ref") != policy_pref
        or gen_sha != prof_sha
    ):
        return None

    if isinstance(expected_block, dict):
        if expected_block.get("schema_version") != expected_checks.EXPECTED_V2:
            return None
        if expected_block.get("projection_source") != "effective_profile":
            return None
        if expected_block.get("authority_status") != "verified":
            return None
        if expected_block.get("profile_ref") != policy_pref:
            return None
        exp_profile = expected_block.get("effective_profile")
        if not isinstance(exp_profile, dict):
            return None
        if (
            exp_profile.get("profile_ref") != policy_pref
            or exp_profile.get("authority_verified") is not True
            or exp_profile.get("profile_sha256") != prof_sha
        ):
            return None
        try:
            validated = expected_checks.validate_effective_profile(
                exp_profile, git=plan.get("git")
            )
            proven = expected_checks._verify_profile_authority(
                validated,
                selection_rule=expected_block.get("selection_rule_ref"),
                hygiene_rule=expected_block.get("hygiene_rule_ref"),
                sources_rule=expected_block.get("sources_rule_ref"),
                config_policy=ep_policy,
            )
        except expected_checks.ExpectedError:
            return None
        if not proven:
            return None
    elif profile_ref is None:
        # Without expected_checks (only narrowed Dagger host adapters drop expected_checks
        # after Plan authority was already validated), require explicit caller profile_ref
        # bound to both inputs.effective_profile and generator.effective_profile_sha256.
        return None

    if profile_ref is not None and profile_ref != policy_pref:
        return None
    return policy_pref


def _relative_existing_files(root, rel_dir):
    resolved_root = Path(root).resolve()
    target = (resolved_root / rel_dir).resolve()
    if not target.is_relative_to(resolved_root):
        return []
    if target.is_file():
        return [target.relative_to(resolved_root).as_posix()]
    if not target.is_dir():
        return []
    out = []
    for path in sorted(target.rglob("*")):
        if path.is_file():
            rel = path.relative_to(resolved_root).as_posix()
            if rel != ".git" and not rel.startswith(".git/"):
                out.append(rel)
    return out


def _load_hygiene_for_closure(plan, resolved_root, hygiene_config=None):
    if hygiene_config is not None and not isinstance(hygiene_config, (str, Path)):
        return hygiene_config
    cfg_path = _hygiene_config_path(plan, resolved_root, hygiene_config)
    if not Path(cfg_path).is_file():
        return None
    try:
        return hygiene.load_config(cfg_path)
    except (OSError, hygiene.HygieneError):
        return None


def _closure_supported(
    name,
    *,
    root,
    execution_context,
    hygiene_config=None,
    item=None,
):
    """Whether this Check's full tool-relevant inputs form a closed, static host set."""
    if execution_context != "host":
        return False
    resolved_root = Path(root).resolve()
    if name in {"whitespace", "forwarding"}:
        return True
    if name == "docs":
        if hygiene_config is None:
            return False
        args = tuple(getattr(hygiene_config.lychee, "arguments", ()) or ())
        if not hygiene.is_offline_lychee_arguments(args):
            return False
        inv = (item or {}).get("inventory")
        return not (isinstance(inv, dict) and inv.get("exit_code") not in (None, 0))
    if name == "tach":
        if hygiene_config is None or hygiene_config.tach.external:
            return False
        tach_path = resolved_root / hygiene_config.tach.path
        return tach_path.is_file()
    return False


def _closure_paths_for_check(
    plan,
    root,
    name,
    item,
    *,
    execution_context="host",
    hygiene_config=None,
    sources_config=None,
    artifact_paths=(),
):
    resolved_root = Path(root).resolve()
    loaded_hyg = (
        _load_hygiene_for_closure(plan, resolved_root, hygiene_config)
        if name in _HYGIENE_CHECKS
        else None
    )
    if not _closure_supported(
        name,
        root=resolved_root,
        execution_context=execution_context,
        hygiene_config=loaded_hyg,
        item=item,
    ):
        return None
    paths: set[str] = set()
    for p in (plan.get("git") or {}).get("changed_paths") or ():
        if isinstance(p, str) and p and source_file_present(resolved_root, p):
            paths.add(p.replace("\\", "/"))
    for p in (item or {}).get("inputs") or ():
        if isinstance(p, str) and p and source_file_present(resolved_root, p):
            paths.add(p.replace("\\", "/"))
    for p in artifact_paths or ():
        if isinstance(p, str) and p and source_file_present(resolved_root, p):
            paths.add(p.replace("\\", "/"))

    def add_rel_file(candidate):
        if candidate is None:
            return
        try:
            res = Path(candidate).resolve()
            if res.is_relative_to(resolved_root) and res.is_file():
                paths.add(res.relative_to(resolved_root).as_posix())
        except OSError:
            pass

    if name in _HYGIENE_CHECKS:
        add_rel_file(_hygiene_config_path(plan, resolved_root, hygiene_config))
        if name == "whitespace":
            add_rel_file(resolved_root / ".gitattributes")
            for p in list(paths):
                cur = (resolved_root / p).parent
                while cur != resolved_root and cur.is_relative_to(resolved_root):
                    add_rel_file(cur / ".gitattributes")
                    cur = cur.parent
        elif name == "docs":
            inv = (item or {}).get("inventory") or {}
            for p in inv.get("files") or ():
                if isinstance(p, str) and (resolved_root / p).is_file():
                    paths.add(p.replace("\\", "/"))
            suffixes = (
                tuple(s.lower() for s in loaded_hyg.docs.suffixes)
                if loaded_hyg is not None
                else (".md", ".html")
            )
            excluded = (
                tuple(loaded_hyg.docs.excluded_prefixes)
                if loaded_hyg is not None
                else ()
            )
            for p in _relative_existing_files(resolved_root, "."):
                if p.lower().endswith(suffixes) and not any(
                    p.startswith(prefix) for prefix in excluded
                ):
                    paths.add(p)
        elif name == "tach":
            tach_rel = loaded_hyg.tach.path if loaded_hyg is not None else "tach.toml"
            add_rel_file(resolved_root / tach_rel)
            add_rel_file(resolved_root / "pyproject.toml")
            try:
                import tomllib

                tach_data = tomllib.loads(
                    (resolved_root / tach_rel).read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                return None
            for sr in tach_data.get("source_roots") or ("src",):
                sr_path = resolved_root / str(sr)
                if not sr_path.exists():
                    return None
                for p in _relative_existing_files(resolved_root, str(sr)):
                    if p.endswith(".py"):
                        paths.add(p)
    return sorted(paths)


def _all_source_entries(root):
    """Enumerate every materialized member under root without following links."""
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        return None
    members = []

    def _walk(directory, prefix):
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError:
            return False
        for entry in entries:
            name = entry.name
            if not prefix and name == ".git":
                continue
            rel = f"{prefix}/{name}" if prefix else name
            try:
                info = os.lstat(entry.path)
            except OSError:
                return False
            is_link = stat.S_ISLNK(info.st_mode) or bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if is_link:
                members.append(rel)
            elif stat.S_ISDIR(info.st_mode):
                if not _walk(entry.path, rel):
                    return False
            else:
                members.append(rel)
        return True

    if not _walk(resolved_root, ""):
        return None
    return sorted(set(members))


def _tree_files_for_snapshot(plan, root, execution_context):
    git = plan.get("git") or {}
    snapshot = str(git.get("snapshot") or "worktree")
    if execution_context == "host" and selection._resolve_git_dir(root) is not None:
        if snapshot == "index":
            env = (
                {"GIT_INDEX_FILE": str(Path(git["index_file"]).resolve())}
                if git.get("index_file")
                else None
            )
            listed = selection._git(root, "ls-files", "-z", env=env)
            if listed.returncode != 0:
                return None
            return sorted({p for p in listed.stdout.split("\0") if p})
        if snapshot in {"commit", "push", "pr_merge"}:
            tree_oid = (
                git.get("execution_tree") or git.get("tree") or git.get("merge_tree")
            )
            if not tree_oid:
                return None
            listed = selection._git(
                root, "ls-tree", "-r", "--name-only", "-z", tree_oid
            )
            if listed.returncode != 0:
                return None
            return sorted(p for p in listed.stdout.split("\0") if p)
    source_tree_id = git.get("source_tree_id")
    if _is_hex64(source_tree_id):
        try:
            actual_tree_id = selection.directory_content_id(root)
        except (OSError, SourcePathError):
            return None
        if actual_tree_id != source_tree_id:
            return None
        return _all_source_entries(root)
    return None


def _relative_view_entries(root_path):
    """Map non-.git relative POSIX paths to ('file', sha256) or ('symlink', link)."""
    root = Path(root_path).resolve()
    entries = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        cur_path = Path(current)
        for d in list(dirnames):
            dp = cur_path / d
            if dp.is_symlink():
                rel = dp.relative_to(root).as_posix()
                entries[rel] = ("symlink", os.readlink(dp))
                dirnames.remove(d)
        for f in sorted(filenames):
            fp = cur_path / f
            rel = fp.relative_to(root).as_posix()
            if fp.is_symlink():
                entries[rel] = ("symlink", os.readlink(fp))
            elif fp.is_file():
                entries[rel] = ("file", hashlib.sha256(fp.read_bytes()).hexdigest())
    return entries


def _verify_consumer_view_closure(base_path, view_path, generated_refs):
    """Prove the Consumer View equals base_path plus only verified generated artifacts."""
    base_root = Path(base_path).resolve()
    view_root = Path(view_path).resolve()
    if base_root == view_root:
        return False
    gen_files = set()
    for gref in generated_refs or ():
        kind = gref.get("input_kind")
        path = gref.get("path")
        sha = gref.get("sha256")
        if kind not in {"file", "directory"} or not path or not _is_hex64(sha):
            return False
        checked = verify_artifact_output(
            view_root, {"kind": kind, "path": path, "sha256": sha}
        )
        if not checked.get("ok"):
            return False
        gen_files.update(checked.get("files") or ())
    base_entries = _relative_view_entries(base_root)
    view_entries = _relative_view_entries(view_root)
    for rel, view_val in view_entries.items():
        if rel in gen_files:
            continue
        base_val = base_entries.get(rel)
        if view_val != base_val:
            if (
                base_val is not None
                and view_val[0] == "symlink"
                and base_val[0] == "symlink"
            ):
                try:
                    if (view_root / rel).resolve().relative_to(view_root) == (
                        base_root / rel
                    ).resolve().relative_to(base_root):
                        continue
                except (OSError, ValueError):
                    pass
            return False
    for rel in base_entries:
        if rel not in gen_files and rel not in view_entries:
            return False
    return True


def _prepare_source_snapshots(
    plan,
    selected,
    *,
    root,
    execution_context,
    hygiene_config=None,
    sources_config=None,
    artifact_paths=(),
    workflow_authority=None,
    base_root=None,
):
    git = plan.get("git") or {}
    snapshot = str(git.get("snapshot") or "worktree")
    base_path = Path(base_root) if base_root is not None else Path(root)
    if snapshot in {"index", "commit", "push", "pr_merge"}:
        tree_files = _tree_files_for_snapshot(plan, base_path, execution_context)
        if tree_files:
            binding = selection.worktree_content_binding(
                base_path, tree_files, require_files=True
            )
            snapshots = {
                "kind": "full",
                "files": tree_files,
                "binding": binding,
            }
            workflow_checks = {
                item["name"]
                for item in selected or ()
                if item.get("name") in _WORKFLOW_SCRIPT_CHECKS
            }
            if workflow_checks:
                tree = (
                    git.get("execution_tree")
                    or git.get("tree")
                    or git.get("index_tree")
                    or git.get("merge_tree")
                )
                git_root = selection._resolve_git_dir(base_path)
                if git_root is None:
                    snapshots["workflow_materializations"] = {
                        name: _workflow_tree_materialization(
                            git, base_path, tree, name, workflow_authority
                        )
                        for name in sorted(workflow_checks)
                    }
                elif (
                    execution_context == "host" or git_root is not None
                ) and _is_git_oid(tree):
                    content_id = selection.tree_content_id(base_path, tree)
                    if (
                        content_id
                        and selection.directory_content_id(base_path) == content_id
                    ):
                        snapshots["workflow_tree_content_id"] = content_id
            return snapshots
        return {"kind": "unproven"}
    per_check = {}
    for item in selected or ():
        name = item.get("name")
        if not name:
            continue
        cpaths = _closure_paths_for_check(
            plan,
            root,
            name,
            item,
            execution_context=execution_context,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            artifact_paths=artifact_paths,
        )
        if cpaths:
            per_check[name] = {
                "files": cpaths,
                "binding": selection.worktree_content_binding(root, cpaths),
            }
    return {"kind": "per_check", "checks": per_check}


def _verified_check_source_ref(
    plan,
    name,
    item,
    *,
    root,
    execution_context,
    source_snapshots,
    hygiene_config=None,
    sources_config=None,
    artifact_paths=(),
    require_execution_started=True,
    base_root=None,
):
    if (
        require_execution_started and item.get("execution_started") is False
    ) or source_snapshots is None:
        return None
    resolved_root = Path(root).resolve()
    base_path = Path(base_root).resolve() if base_root is not None else resolved_root
    gen_refs = list(item.get("generated_input_refs") or ())
    retained = bool(item.get("generated_inputs_retained"))
    if gen_refs and not retained and name in _WORKFLOW_SCRIPT_CHECKS:
        return None
    loaded_hyg = (
        _load_hygiene_for_closure(plan, resolved_root, hygiene_config)
        if name in _HYGIENE_CHECKS
        else None
    )
    workflow_materialization = (
        source_snapshots.get("workflow_materializations") or {}
    ).get(name)
    workflow_tree = None
    external_targets = []
    if name in _WORKFLOW_SCRIPT_CHECKS:
        workflow_tree = workflow_materialization or source_snapshots.get(
            "workflow_tree_content_id"
        )
        external_targets = [
            target
            for target in item.get("target_sources", ())
            if target["policy_scope"] == "event_external"
        ]
    if workflow_tree:
        config_input = (plan.get("inputs") or {}).get("config") or {}
        workflow_config = (config_input.get("normalized") or {}).get(
            "workflow_checks"
        ) or {}
        if (
            config_input.get("workflow_external_configs")
            or config_input.get("policy_scope") == "event_external"
            or (name == "zizmor" and not workflow_config.get("zizmor_offline", True))
            or item.get("target_source_problem")
            or selection.directory_content_id(base_path) != workflow_tree
        ):
            return None
    elif (
        not (
            name == "whitespace"
            and _uses_exported_index_comparison(
                plan, execution_context, extra_paths=artifact_paths
            )
            and resolved_root == base_path
            and item.get("git_whitespace_comparison") is not None
        )
        and not _closure_supported(
            name,
            root=resolved_root,
            execution_context=execution_context,
            hygiene_config=loaded_hyg,
            item=item,
        )
        and not _closed_tree_check(
            plan, name, resolved_root, base_path, execution_context, sources_config
        )
    ):
        return None
    git = plan.get("git") or {}
    snapshot = str(git.get("snapshot") or "worktree")
    declared = (
        git.get("execution_tree")
        or git.get("tree")
        or git.get("index_tree")
        or git.get("merge_tree")
    )
    actual = "worktree" if execution_context == "host" else "dagger_directory"
    if snapshot in {"index", "commit", "push", "pr_merge"} and (
        source_snapshots.get("kind") != "full"
    ):
        return None
    if source_snapshots.get("kind") == "full":
        files = source_snapshots["files"]
        before = source_snapshots["binding"]
        # Workflow source authority separately proves the selected external
        # targets, while workflow_tree binds the literal repository link entries.
        authorized_external = (
            {target["path"] for target in external_targets} if workflow_tree else set()
        )
        if set(before.get("unverified_paths") or ()) - authorized_external:
            return None
        if workflow_materialization:
            if execution_context == "host":
                actual = "materialized_directory"
        elif snapshot == "index" and execution_context == "host":
            exp_tree = declared
            if selection.index_source_refusal(
                base_path, exp_tree, index_file=git.get("index_file")
            ):
                return None
            actual = "index_verified_worktree"
        elif snapshot in {"commit", "push", "pr_merge"} and execution_context == "host":
            if selection.worktree_tree_refusal(base_path, declared):
                return None
            head_commit = git.get("head_commit")
            if (
                snapshot in {"commit", "push"}
                and head_commit
                and selection._resolve(base_path, "HEAD") != head_commit
            ):
                return None
        elif (
            execution_context == "dagger_export"
            and git.get("source_tree_id")
            and selection.directory_content_id(base_path) != git["source_tree_id"]
        ):
            return None
        after = selection.worktree_content_binding(base_path, files, require_files=True)
        if after != before:
            return None
        if gen_refs and not _verify_consumer_view_closure(
            base_path, resolved_root, gen_refs
        ):
            return None
        mat_digest = workflow_tree or (
            str(git["source_tree_id"])
            if git.get("source_tree_id")
            else str(before["digest"])
        )
        if external_targets or gen_refs:
            # The verified repository and independently proven external/generated
            # inputs together close this Check's inputs.
            sorted_gen = sorted(
                gen_refs,
                key=lambda ref: (
                    str(ref.get("path") or ""),
                    str(ref.get("input_kind") or ""),
                    str(ref.get("sha256") or ""),
                ),
            )
            digest_payload = {"directory_content_id": mat_digest}
            if external_targets:
                digest_payload["external_scan_targets"] = external_targets
            if sorted_gen:
                digest_payload["generated_inputs"] = sorted_gen
            scope = [*files]
            for target in external_targets:
                if target["path"] not in scope:
                    scope.append(target["path"])
            for gpath in sorted(
                {
                    *(str(p).replace("\\", "/") for p in (artifact_paths or ()) if p),
                    *(
                        str(ref.get("path") or "").replace("\\", "/")
                        for ref in sorted_gen
                        if ref.get("path")
                    ),
                }
            ):
                if gpath and gpath not in scope:
                    scope.append(gpath)
            return check_contract.source_ref(
                snapshot,
                declared_target_tree=declared,
                actual_execution_source=actual,
                head_commit=git.get("head_commit"),
                index_tree=git.get("index_tree"),
                content_digest=_canonical_sha256(digest_payload).removeprefix(
                    "sha256:"
                ),
                verified_scope=scope,
                binding_status="content_digest",
                scope_kind="check_input_closure",
                materialization_ref=f"sha256:{mat_digest}",
            )
        return check_contract.source_ref(
            snapshot,
            declared_target_tree=declared,
            actual_execution_source=actual,
            head_commit=git.get("head_commit"),
            index_tree=git.get("index_tree"),
            content_digest=before["digest"],
            verified_scope=files,
            binding_status="byte_tree",
            scope_kind="full_input_closure",
            materialization_ref=f"sha256:{mat_digest}",
        )
    entry = (source_snapshots.get("checks") or {}).get(name)
    if entry is None:
        return None
    before_files = entry["files"]
    before = entry["binding"]
    if before.get("unverified_paths"):
        return None
    if (
        base_path != resolved_root
        and gen_refs
        and not _verify_consumer_view_closure(base_path, resolved_root, gen_refs)
    ):
        return None
    if name == "docs" and isinstance((item or {}).get("inventory"), dict):
        # Also include any discovered inventory files that did not exist under '.' scan.
        final_files = _closure_paths_for_check(
            plan,
            root,
            name,
            item,
            execution_context=execution_context,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            artifact_paths=artifact_paths,
        )
        if final_files is None or set(final_files) != set(before_files):
            return None
    after = selection.worktree_content_binding(root, before_files)
    if after != before:
        return None
    plan_binding = (git.get("content_binding") or {}).get("paths")
    if isinstance(plan_binding, dict):
        for rel_path, expected_sha in plan_binding.items():
            if (
                expected_sha is not None
                and before["paths"].get(rel_path) != expected_sha
            ):
                return None
    return check_contract.source_ref(
        snapshot,
        declared_target_tree=declared,
        actual_execution_source=actual,
        head_commit=git.get("head_commit"),
        index_tree=git.get("index_tree"),
        content_digest=before["digest"],
        verified_scope=before_files,
        binding_status="content_digest",
        scope_kind="check_input_closure",
    )


def _canonical_sha256(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _closed_tree_check(plan, name, root, base_path, execution_context, sources_config):
    git = plan["git"]
    if (
        name not in {"forwarding", "source", "unittest", "pytest"}
        or root != base_path
        or git.get("snapshot") not in {"index", "commit", "push", "pr_merge"}
        or not git.get("source_tree_id")
        or selection.directory_content_id(root) != git["source_tree_id"]
    ):
        return False
    if name == "forwarding":
        return True
    if name == "source":
        config, path, _, error = _load_verified_config(
            plan,
            root,
            sources_config,
            key="sources",
            default_name="ci-sources.toml",
            loader=sources.load_config,
            error_type=sources.SourcesError,
            identity_fn=sources.config_identity,
        )
        return (
            error is None
            and config is not None
            and not config.require_text_unset
            and path.is_relative_to(root)
            and all(
                (root / candidate).resolve().is_relative_to(root)
                for candidate in (config.manifest, *config.acquired_roots)
            )
        )
    chosen = plan["selection"]
    return (
        name == chosen.get("framework", "unittest")
        and bool(chosen.get("tests"))
        and (root / chosen.get("src_root", "src")).resolve().is_relative_to(root)
        and (
            execution_context == "dagger_export"
            or not (root / "pyproject.toml").is_file()
        )
    )


def _snapshot_binding_for_check(snapshots, check_name):
    """Return the effective-view content binding used for one Check SourceRef."""
    if not isinstance(snapshots, dict):
        return None
    if snapshots.get("kind") == "full":
        binding = snapshots.get("binding")
        return binding if isinstance(binding, dict) else None
    if snapshots.get("kind") == "per_check":
        entry = (snapshots.get("checks") or {}).get(check_name)
        if not isinstance(entry, dict):
            return None
        binding = entry.get("binding")
        return binding if isinstance(binding, dict) else None
    return None


def _subset_digest_from_binding(binding, subset_paths, *, absent_paths=()):
    """Digest a path subset using an existing snapshot binding's path hashes.

    Uses the same canonical encoding as ``selection.worktree_content_binding`` so
    the subset digest and full-closure digest describe one effective input view.
    """
    if not isinstance(binding, dict) or not isinstance(binding.get("paths"), dict):
        return None
    if not subset_paths:
        return None
    paths = binding["paths"]
    absent = {str(item).replace("\\", "/") for item in (absent_paths or ())}
    entries = {}
    for path in sorted({str(item).replace("\\", "/") for item in subset_paths}):
        if path in paths:
            entries[path] = paths[path]
        elif path in absent:
            entries[path] = None
        else:
            return None
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verified_deleted_scope(
    plan,
    expected_source,
    resolved_source,
    snapshots,
    *,
    root,
    execution_context,
    verified_overlay_paths=(),
):
    """Return deleted changed paths proven absent in the verified target snapshot."""
    if (
        not isinstance(expected_source, dict)
        or not isinstance(resolved_source, dict)
        or expected_source.get("scope_kind") != "changed_paths"
        or resolved_source.get("scope_kind")
        not in {"check_input_closure", "full_input_closure"}
        or resolved_source.get("binding_status") not in {"byte_tree", "content_digest"}
    ):
        return ()
    git = plan.get("git") or {}
    declared_deleted = {
        str(path).replace("\\", "/")
        for path in (git.get("deleted_paths") or ())
        if isinstance(path, str) and path
    }
    snapshot = str(git.get("snapshot") or "worktree")
    exp_scope = {
        str(path).replace("\\", "/")
        for path in (expected_source.get("verified_input_scope") or ())
        if isinstance(path, str) and path
    }
    res_scope = {
        str(path).replace("\\", "/")
        for path in (resolved_source.get("verified_input_scope") or ())
        if isinstance(path, str) and path
    }
    root_path = Path(root)
    overlays = {
        str(path).replace("\\", "/")
        for path in (verified_overlay_paths or ())
        if isinstance(path, str) and path
    }
    if isinstance(snapshots, dict) and snapshots.get("kind") == "full":
        if not declared_deleted:
            return ()
        candidates = sorted((exp_scope & declared_deleted) - res_scope)
        if not candidates:
            return ()
        tree_files = {
            str(path).replace("\\", "/") for path in (snapshots.get("files") or ())
        }
        return tuple(
            path
            for path in candidates
            if path not in tree_files
            and not _overlay_intersects_subset(overlays, [path])
        )
    if snapshot != "worktree":
        return ()
    content_binding = git.get("content_binding") or {}
    binding_paths = content_binding.get("paths")
    unverified_binding = {
        str(path).replace("\\", "/")
        for path in (content_binding.get("unverified_paths") or ())
        if isinstance(path, str) and path
    }
    candidates = sorted(exp_scope - res_scope)
    if not candidates:
        return ()
    verified = []
    for path in candidates:
        if _overlay_intersects_subset(overlays, [path]) or path in unverified_binding:
            continue
        if isinstance(binding_paths, dict):
            if path not in binding_paths or binding_paths[path] is not None:
                continue
        elif path not in declared_deleted:
            continue
        if source_file_present(root_path, path):
            continue
        verified.append(path)
    return tuple(verified)


def _overlay_intersects_subset(overlay_paths, subset_paths):
    """Return subset paths covered by verified producer overlay targets."""
    overlays = {
        str(path).replace("\\", "/").rstrip("/") for path in (overlay_paths or ())
    }
    hits = []
    for raw in subset_paths or ():
        path = str(raw).replace("\\", "/").rstrip("/")
        if any(
            path == overlay
            or path.startswith(overlay + "/")
            or overlay.startswith(path + "/")
            for overlay in overlays
        ):
            hits.append(str(raw).replace("\\", "/"))
    return tuple(sorted(hits))


def _scope_plan_for_pre_execution(
    plan,
    check_name,
    *,
    expected_check_ref=None,
    scope_id=None,
):
    git = plan.get("git") or {}
    expected_block = plan.get("expected_checks")
    sel = plan.get("selection") or {}
    is_multi = (
        git.get("snapshot") == "multi"
        or isinstance(sel.get("per_ref"), list)
        and bool(sel.get("per_ref"))
        or isinstance(expected_block, dict)
        and isinstance(expected_block.get("per_ref"), list)
        and bool(expected_block.get("per_ref"))
    )
    if not is_multi:
        matched_expected = None
        expected_scope = None
        if isinstance(expected_block, dict):
            try:
                matched_expected, expected_scope = _locate_expected_entry(
                    expected_block,
                    check_name,
                    expected_check_ref=expected_check_ref,
                    scope_id=scope_id,
                )
            except ExecutionError as error:
                if error.code == "source_resolution_scope_required":
                    raise ExecutionError("pre_execution_scope_required") from None
                raise ExecutionError(
                    "pre_execution_check_scope_ambiguous_or_missing"
                ) from None
        return plan, matched_expected, expected_scope

    matched_expected = None
    expected_scope = None
    if isinstance(expected_block, dict):
        try:
            matched_expected, expected_scope = _locate_expected_entry(
                expected_block,
                check_name,
                expected_check_ref=expected_check_ref,
                scope_id=scope_id,
            )
        except ExecutionError as error:
            if error.code == "source_resolution_scope_required":
                raise ExecutionError("pre_execution_scope_required") from None
            raise ExecutionError(
                "pre_execution_check_scope_ambiguous_or_missing"
            ) from None

    sel_per_ref = list(sel.get("per_ref") or ())
    ref_entry = None
    if sel_per_ref:
        if expected_scope is not None:
            exp_src = expected_scope.get("source") or {}
            exp_ev = expected_scope.get("event_ref") or {}
            exp_tree = exp_src.get("declared_target_tree")
            exp_head = exp_src.get("head_commit") or exp_ev.get("head_commit")
            exp_ref_label = expected_scope.get("source_ref")
            exp_base = exp_ev.get("base_commit")
            matches = [
                cand
                for cand in sel_per_ref
                if isinstance(cand, dict)
                and (exp_tree is None or cand.get("tree") == exp_tree)
                and (exp_head is None or cand.get("after") == exp_head)
                and (
                    not exp_ref_label
                    or cand.get("source_ref") == exp_ref_label
                    or cand.get("remote_ref") == exp_ref_label
                )
                and (
                    not exp_base
                    or not cand.get("before")
                    or cand.get("before") == exp_base
                    or cand.get("baseline_ref") == exp_base
                )
            ]
            if len(matches) == 1:
                ref_entry = matches[0]
            elif len(matches) > 1:
                raise ExecutionError("pre_execution_scope_required")
        elif len(sel_per_ref) == 1:
            ref_entry = sel_per_ref[0]
        else:
            raise ExecutionError("pre_execution_scope_required")

    scoped_git = dict(git)
    if expected_scope is not None:
        exp_src = expected_scope.get("source") or {}
        exp_ev = expected_scope.get("event_ref") or {}
        exp_base_ref = (
            (matched_expected or {}).get("baseline_ref")
            if isinstance(matched_expected, dict)
            else None
        ) or {}
        scoped_git["snapshot"] = exp_src.get("snapshot") or "push"
        scoped_git["tree"] = (
            ref_entry.get("tree") if isinstance(ref_entry, dict) else None
        ) or exp_src.get("declared_target_tree")
        scoped_git["execution_tree"] = scoped_git["tree"]
        scoped_git["head_commit"] = (
            (ref_entry.get("after") if isinstance(ref_entry, dict) else None)
            or exp_src.get("head_commit")
            or exp_ev.get("head_commit")
        )
        base_commit = (
            exp_base_ref.get("base_commit") if isinstance(exp_base_ref, dict) else None
        ) or exp_ev.get("base_commit")
        if base_commit:
            scoped_git["base_commit"] = base_commit
        else:
            scoped_git.pop("base_commit", None)
        ref_action = (
            ref_entry.get("ref_action") if isinstance(ref_entry, dict) else None
        ) or expected_scope.get("ref_action")
        if ref_action is not None:
            scoped_git["ref_action"] = ref_action
        baseline_status = (
            (ref_entry.get("baseline_status") if isinstance(ref_entry, dict) else None)
            or (
                exp_base_ref.get("baseline_status")
                if isinstance(exp_base_ref, dict)
                else None
            )
            or expected_scope.get("baseline_status")
        )
        if baseline_status is not None:
            scoped_git["baseline_status"] = baseline_status
        scoped_git.pop("content_binding", None)
    elif isinstance(ref_entry, dict):
        scoped_git["snapshot"] = "push"
        scoped_git["tree"] = ref_entry.get("tree")
        scoped_git["execution_tree"] = ref_entry.get("tree")
        if ref_entry.get("after"):
            scoped_git["head_commit"] = ref_entry["after"]
        if ref_entry.get("before"):
            scoped_git["base_commit"] = ref_entry["before"]
        if ref_entry.get("ref_action") is not None:
            scoped_git["ref_action"] = ref_entry["ref_action"]
        if ref_entry.get("baseline_status") is not None:
            scoped_git["baseline_status"] = ref_entry["baseline_status"]
        scoped_git.pop("content_binding", None)

    scoped_check_scope = (
        ref_entry.get("check_scope")
        if isinstance(ref_entry, dict) and ref_entry.get("check_scope") is not None
        else list((expected_scope or {}).get("checks") or ())
    )
    scoped_sel = {**sel, "check_scope": scoped_check_scope}
    scoped_sel.pop("per_ref", None)
    scoped_plan = {
        **plan,
        "git": scoped_git,
        "selection": scoped_sel,
        "check_scope": scoped_check_scope,
    }
    if expected_scope is not None:
        scoped_expected = {
            **(expected_block if isinstance(expected_block, dict) else {}),
            "scope_id": expected_scope.get("scope_id"),
            "checks": list(expected_scope.get("checks") or ()),
        }
        scoped_expected.pop("per_ref", None)
        scoped_plan["expected_checks"] = scoped_expected
    return scoped_plan, matched_expected, expected_scope


def _pre_execution_check_item(scoped_plan, check_name):
    selected = _selected_checks(scoped_plan)
    matches = [item for item in selected if item.get("name") == check_name]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        raise ExecutionError("pre_execution_check_scope_ambiguous_or_missing")
    sel = scoped_plan.get("selection") or {}
    all_scope = sel.get("check_scope") or scoped_plan.get("check_scope") or ()
    non_sel = [
        item
        for item in all_scope
        if isinstance(item, dict) and item.get("name") == check_name
    ]
    if len(non_sel) == 1:
        status = str(non_sel[0].get("status") or "unknown")
        return non_sel[0], f"pre_execution_check_{status}"
    raise ExecutionError("pre_execution_check_scope_ambiguous_or_missing")


def _git_baseline_source_ref(root, baseline, *, base_ref):
    if check_contract.is_unborn_index_baseline(baseline):
        tree = selection.unborn_index_base(root, base=base_ref)
        if tree != baseline["base_tree"]:
            return None
        return {
            "name": "git-odb-empty-tree",
            "version": "1",
            "sha256": hashlib.sha256(tree.encode()).hexdigest(),
        }
    commit = baseline.get("base_commit")
    if not commit:
        return None
    odb_tree = selection._git(root, "rev-parse", "--verify", f"{commit}^{{tree}}")
    tree = odb_tree.stdout.strip()
    if odb_tree.returncode != 0 or not _is_git_oid(tree):
        return None
    return {
        "name": "git-odb-commit",
        "version": "1",
        "sha256": hashlib.sha256(f"{commit}:{tree}".encode()).hexdigest(),
    }


def _resolve_pre_execution_baseline(
    scoped_plan,
    matched_expected,
    definition,
    *,
    resolved_root,
):
    git = scoped_plan.get("git") or {}
    exp_base = (
        matched_expected.get("baseline_ref")
        if isinstance(matched_expected, dict)
        else None
    )
    plan_base = _baseline_reference(scoped_plan)
    baseline = plan_base if isinstance(plan_base, dict) else exp_base
    ref_baseline_status = git.get("baseline_status") or (
        exp_base.get("baseline_status") if isinstance(exp_base, dict) else None
    )
    needs_baseline = bool(definition is not None and definition.requires_baseline)
    if ref_baseline_status in {"unknown", "old_object_unavailable"} and (
        needs_baseline or baseline is not None
    ):
        return baseline, None, f"pre_execution_baseline_{ref_baseline_status}"
    has_baseline = isinstance(baseline, dict) and (
        baseline.get("base_commit") or check_contract.is_unborn_index_baseline(baseline)
    )
    if not has_baseline:
        if needs_baseline:
            return None, None, "pre_execution_baseline_missing"
        return baseline, None, None
    baseline_source_ref = _git_baseline_source_ref(
        resolved_root, baseline, base_ref=git.get("base_ref")
    )
    if baseline_source_ref is None:
        return baseline, None, "baseline_object_unavailable"
    return baseline, baseline_source_ref, None


def _resolve_pre_execution_rule(
    scoped_plan,
    check_name,
    matched_expected,
    *,
    root,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
):
    inputs = scoped_plan.get("inputs")
    has_bound_input = isinstance(inputs, dict) and (
        (check_name in _HYGIENE_CHECKS and "hygiene" in inputs)
        or (check_name == "source" and "sources" in inputs)
        or (
            check_name not in _HYGIENE_CHECKS
            and check_name != "source"
            and "config" in inputs
        )
    )
    if has_bound_input:
        try:
            rule_ref, rule_source_ref, _ = _independent_rule_for_check(
                scoped_plan,
                check_name,
                root=root,
                hygiene_config=hygiene_config,
                sources_config=sources_config,
                selection_config=selection_config,
            )
        except ExecutionError as error:
            return None, None, error.code
        exp_rule = (matched_expected or {}).get("rule_ref")
        if exp_rule is not None and rule_ref != exp_rule:
            return None, None, "pre_execution_rule_mismatch"
        return rule_ref, rule_source_ref, None
    if check_name in _HYGIENE_CHECKS:
        cfg_path = _hygiene_config_path(
            scoped_plan, Path(root).resolve(), hygiene_config
        )
        if Path(cfg_path).is_file():
            try:
                loaded = _load_hygiene_for_closure(
                    scoped_plan, Path(root).resolve(), hygiene_config
                )
                ident = hygiene.config_identity(cfg_path, loaded, root=root)
                rule_ref = dict(ident["effective_check_config_ref"])
                exp_rule = (matched_expected or {}).get("rule_ref")
                if exp_rule is not None and rule_ref != exp_rule:
                    return None, None, "pre_execution_rule_mismatch"
                return rule_ref, None, None
            except (OSError, hygiene.HygieneError, check_contract.CheckContractError):
                return None, None, "hygiene_config_invalid"
    return None, None, None


def _pre_execution_unproven_reason(
    scoped_plan,
    check_name,
    item,
    *,
    root,
    execution_context,
    hygiene_config=None,
):
    if execution_context != "host":
        return f"{execution_context}_pre_execution_unverified"
    git = scoped_plan.get("git") or {}
    snapshot = str(git.get("snapshot") or "worktree")
    declared = (
        git.get("execution_tree")
        or git.get("tree")
        or git.get("index_tree")
        or git.get("merge_tree")
    )
    if snapshot == "index":
        refusal = selection.index_source_refusal(
            root, declared, index_file=git.get("index_file")
        )
        if refusal:
            return refusal
    elif snapshot in {"commit", "push", "pr_merge"}:
        refusal = selection.worktree_tree_refusal(root, declared)
        if refusal:
            return refusal
        head_commit = git.get("head_commit")
        if (
            snapshot in {"commit", "push"}
            and head_commit
            and selection._resolve(root, "HEAD") != head_commit
        ):
            return "head_commit_mismatch"
    loaded_hyg = (
        _load_hygiene_for_closure(scoped_plan, Path(root).resolve(), hygiene_config)
        if check_name in _HYGIENE_CHECKS
        else None
    )
    if not _closure_supported(
        check_name,
        root=root,
        execution_context=execution_context,
        hygiene_config=loaded_hyg,
        item=item,
    ):
        return "pre_execution_input_closure_unproven"
    return "pre_execution_input_closure_unproven"


def _active_tool_identities(definition, rule_ref):
    if definition is None or not definition.tool_requirements:
        return []
    if check_contract._unresolved_conditions(definition, rule=rule_ref):
        return None
    active = check_contract._requirement_order(
        check_contract._active_requirements(definition, rule=rule_ref)
    )
    return [
        {
            "requirement_id": req.requirement_id,
            "after": list(req.after),
            "alternatives": [
                {
                    "access_id": alt.access_id,
                    "tool_id": alt.tool_id,
                    "tool_id_source": alt.tool_id_source,
                    "version": alt.version,
                    "artifact_sha256": alt.artifact_sha256,
                    "authority": alt.authority,
                }
                for alt in req.alternatives
            ],
        }
        for req in active
    ]


def _effective_git_whitespace_config(root, paths=(), *, tree=None):
    if root is None or selection._resolve_git_dir(root) is None:
        return None, "whitespace_config_unreadable"
    try:
        return git_source.whitespace_settings(root, paths, tree=tree), None
    except (git_source.GitSourceError, OSError):
        return None, "whitespace_config_unreadable"


def _uses_exported_index_comparison(plan, execution_context, *, extra_paths=()):
    return (
        execution_context == "dagger_export"
        and plan["git"].get("snapshot") == "index"
        and bool(plan["git"].get("source_tree_id"))
        and not extra_paths
    )


def _run_exported_index_whitespace(root, git, paths, runner):
    if not paths:
        return hygiene._result(
            "whitespace", "not_applicable", reason="no_changed_paths"
        )
    outcome = (
        runner(git, paths)
        if runner is not None
        else git_source.run_exported_index_whitespace(root, git, paths)
    )
    code = outcome.get("exit_code")
    if type(code) is not int:
        return {
            "name": "whitespace",
            "status": "not_executed",
            "reason": "whitespace_comparison_inputs_unverified",
            "stderr": outcome.get("stderr") or "",
            "execution_started": False,
        }
    result = hygiene.whitespace_result(
        code, stdout=outcome["stdout"], stderr=outcome["stderr"]
    )
    comparison = outcome.get("comparison")
    if comparison is not None:
        result["git_whitespace_comparison"] = comparison
        result["git_whitespace_config"] = comparison["git_config"]
    else:
        result["git_whitespace_config_drifted"] = True
    return result


def _resolve_exported_index_whitespace(root, git, paths):
    try:
        with git_source.isolated_index_comparison(root, git, exported=False) as (
            checkout,
            env,
            base,
        ):
            return git_source.whitespace_comparison_ref(
                checkout, env, base=base, target=git["execution_tree"], paths=paths
            )
    except (git_source.GitSourceError, OSError) as error:
        raise ExecutionError("whitespace_comparison_inputs_unverified") from error


def _verify_pre_execution_prerequisites(
    incoming,
    check_name,
    *,
    artifact_root=None,
    prerequisite_results=None,
):
    prereq_inputs = []
    verified_files = []
    seen_paths = {}
    consumer_ref = {"kind": "check", "name": check_name}
    for edge in incoming:
        p_ref = edge.get("producer_ref") or {}
        p_kind, p_name = p_ref.get("kind"), p_ref.get("name")
        if edge.get("dependency_kind") == "data":
            if p_kind != "prerequisite_action":
                return None, None, "data_check_producer_unsupported"
            if artifact_root is None or not isinstance(prerequisite_results, dict):
                return None, None, "pre_execution_prerequisite_unproven"
            producer_res = prerequisite_results.get((p_kind, p_name))
            if producer_res is None:
                producer_res = prerequisite_results.get(p_name)
            if not isinstance(producer_res, dict):
                return None, None, "pre_execution_prerequisite_unproven"
            try:
                p_root = producer_artifact_root(artifact_root, p_ref)
            except ValueError:
                return None, None, "pre_execution_prerequisite_unproven"
            ok, reason, closure = evaluate_edge(
                edge, producer_res, artifact_root=p_root
            )
            if not ok or closure is None or not closure.get("verified"):
                return None, None, reason or "pre_execution_prerequisite_unproven"
            for obj in closure.get("materialized_objects") or ():
                rel = obj["path"]
                if (rel in seen_paths and seen_paths[rel] != str(p_root)) or any(
                    _artifact_paths_overlap(rel, existing) and owner != str(p_root)
                    for existing, owner in seen_paths.items()
                ):
                    return None, None, "data_artifact_conflict"
                seen_paths[rel] = str(p_root)
                verified_files.append((p_root, rel, obj["kind"]))
            gen_refs = generated_input_refs([closure], consumer_ref)
            prereq_inputs.append(
                {
                    "producer_ref": p_ref,
                    "dependency_kind": edge.get("dependency_kind"),
                    "output_ref": edge.get("output_ref"),
                    "generated_input_refs": gen_refs,
                }
            )
        else:
            prereq_inputs.append(
                {
                    "producer_ref": p_ref,
                    "dependency_kind": edge.get("dependency_kind"),
                    "output_ref": edge.get("output_ref"),
                }
            )
    return prereq_inputs, verified_files, None


def build_pre_execution_source_proof(
    plan,
    check_name,
    *,
    root,
    expected_check_ref=None,
    scope_id=None,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    execution_context="host",
    artifact_paths=(),
    prerequisite_results=None,
    artifact_root=None,
):
    """Resolve a candidate Host Check input closure *before* native execution.

    This is a content proof for the proposed target inputs, not a witness that
    the tool executed or actually read them. An unsupported/dynamic closure,
    drifted config/snapshot, or missing/unreadable Git ODB baseline returns an
    explicit unverified record. Reuse consumers (#63 T2) must independently
    verify issuer trust and compare these inputs against historical execution.
    """
    if not isinstance(plan, dict) or not isinstance(check_name, str) or not check_name:
        raise ExecutionError("pre_execution_check_scope_ambiguous_or_missing")
    resolved_root = Path(root).resolve()
    scoped_plan, matched_expected, expected_scope = _scope_plan_for_pre_execution(
        plan,
        check_name,
        expected_check_ref=expected_check_ref,
        scope_id=scope_id,
    )
    item, item_reason = _pre_execution_check_item(scoped_plan, check_name)
    definition = check_contract.definition_for(check_name)
    bound_scope_id = (expected_scope or {}).get("scope_id") or scope_id
    bound_expected_ref = (matched_expected or {}).get(
        "expected_check_ref"
    ) or expected_check_ref

    def unverified(reason):
        return {
            "phase": "pre_execution",
            "check_name": check_name,
            "check_id": definition.check_id if definition is not None else None,
            "definition_version": (
                definition.definition_version if definition is not None else None
            ),
            "scope_id": bound_scope_id,
            "expected_check_ref": bound_expected_ref,
            "execution_context": execution_context,
            "execution_started": False,
            "trusted": False,
            "required": False,
            "status": "unverified",
            "reason": reason,
            "source_ref": None,
        }

    if item_reason is not None:
        return unverified(item_reason)

    if execution_context != "host":
        return unverified(f"{execution_context}_pre_execution_unverified")

    bound_ok, bind_reason, _ = _verify_execution_binding(
        scoped_plan, resolved_root, execution_context=execution_context
    )
    if not bound_ok:
        return unverified(bind_reason or "pre_execution_binding_failed")

    rule_ref, rule_source_ref, rule_reason = _resolve_pre_execution_rule(
        scoped_plan,
        check_name,
        matched_expected,
        root=resolved_root,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
        selection_config=selection_config,
    )
    if rule_reason is not None:
        return unverified(rule_reason)

    profile_ref = _profile_reference(
        scoped_plan, root=resolved_root, execution_context=execution_context
    )
    if (
        isinstance(matched_expected, dict)
        and matched_expected.get("profile_ref") is not None
        and profile_ref != matched_expected.get("profile_ref")
    ):
        return unverified("pre_execution_profile_unverified")

    baseline_ref, baseline_source_ref, baseline_reason = (
        _resolve_pre_execution_baseline(
            scoped_plan,
            matched_expected,
            definition,
            resolved_root=resolved_root,
        )
    )
    if baseline_reason is not None:
        return unverified(baseline_reason)

    incoming = pre_execution_edges(scoped_plan).get(("check", check_name), ())
    prereq_inputs, verified_files, prereq_reason = _verify_pre_execution_prerequisites(
        incoming,
        check_name,
        artifact_root=artifact_root,
        prerequisite_results=prerequisite_results,
    )
    if prereq_reason is not None:
        return unverified(prereq_reason)

    env_inputs = None
    if check_name == "whitespace":
        changes = _changeset_from_plan(
            scoped_plan,
            resolved_root,
            extra_paths=artifact_target_paths(verified_files),
        )
        env_inputs, ws_err = _effective_git_whitespace_config(
            resolved_root,
            changes.paths,
            tree=changes.index_tree if changes.base_tree is not None else None,
        )
        if ws_err is not None:
            return unverified(ws_err)

    tool_identities = _active_tool_identities(definition, rule_ref)
    if tool_identities is None:
        return unverified("pre_execution_rule_conditions_unresolved")

    # Never fold unverified caller-supplied artifact_paths into file closure
    # when incoming data edges require producer artifact verification.
    # When verified producer artifacts exist, mirror _execute_with_dependencies()
    # via build_consumer_view() so overlaid files match host execution views.
    has_data_edge = any(edge.get("dependency_kind") == "data" for edge in incoming)
    temporary = None
    unproven_reason = None
    changed_paths_content_digest = None
    overlay_subset_paths = ()
    verified_deleted = ()
    try:
        if verified_files:
            temporary, effective_root = build_consumer_view(
                resolved_root, verified_files
            )
            effective_plan = _rebased_consumer_plan(scoped_plan, resolved_root)
            effective_artifact_paths = artifact_target_paths(verified_files)
        else:
            effective_root = resolved_root
            effective_plan = scoped_plan
            effective_artifact_paths = () if has_data_edge else artifact_paths
        snapshots = _prepare_source_snapshots(
            effective_plan,
            [item],
            root=effective_root,
            execution_context=execution_context,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            artifact_paths=effective_artifact_paths,
        )
        source = _verified_check_source_ref(
            effective_plan,
            check_name,
            item,
            root=effective_root,
            execution_context=execution_context,
            source_snapshots=snapshots,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            artifact_paths=effective_artifact_paths,
            require_execution_started=False,
        )
        if source is None:
            unproven_reason = _pre_execution_unproven_reason(
                effective_plan,
                check_name,
                item,
                root=effective_root,
                execution_context=execution_context,
                hygiene_config=hygiene_config,
            )
        else:
            # Derive the Expected changed-path subset digest from the same
            # effective-view snapshot binding that produced the closed SourceRef.
            # Do not re-hash resolved_root after the consumer view is gone.
            expected_source = (
                matched_expected.get("source_ref")
                if isinstance(matched_expected, dict)
                else None
            )
            verified_deleted = _verified_deleted_scope(
                effective_plan,
                expected_source,
                source,
                snapshots,
                root=effective_root,
                execution_context=execution_context,
                verified_overlay_paths=effective_artifact_paths,
            )
            if (
                isinstance(expected_source, dict)
                and expected_source.get("scope_kind") == "changed_paths"
                and source.get("scope_kind")
                in {"check_input_closure", "full_input_closure"}
            ):
                subset = list(expected_source.get("verified_input_scope") or ())
                snapshot_binding = _snapshot_binding_for_check(snapshots, check_name)
                subset_digest = _subset_digest_from_binding(
                    snapshot_binding,
                    subset,
                    absent_paths=verified_deleted,
                )
                if subset_digest is None:
                    source = None
                    unproven_reason = "pre_execution_changed_paths_digest_unproven"
                else:
                    expected_digest = expected_source.get("content_digest")
                    if expected_digest is not None and expected_digest != subset_digest:
                        source = None
                        unproven_reason = "pre_execution_changed_paths_digest_mismatch"
                    else:
                        changed_paths_content_digest = subset_digest
                        overlay_subset_paths = _overlay_intersects_subset(
                            effective_artifact_paths, subset
                        )
    finally:
        if temporary is not None:
            temporary.cleanup()
    if source is None:
        return unverified(unproven_reason)
    source = dict(source)
    # byte_tree denotes a materialized execution view. A planned source
    # snapshot can prove the relevant bytes without pretending a tool ran.
    source["actual_execution_source"] = "planned_host_worktree"
    if source["binding_status"] == "byte_tree":
        source["binding_status"] = "content_digest"
        source.pop("materialization_ref", None)
    check_contract.validate_source_ref(source)

    closure_payload = {
        "check_id": definition.check_id if definition is not None else None,
        "definition_version": (
            definition.definition_version if definition is not None else None
        ),
        "execution_context": execution_context,
        "scope_kind": source["scope_kind"],
        "snapshot": source["snapshot"],
        "content_digest": source["content_digest"],
        "verified_input_scope": source["verified_input_scope"],
        "declared_target_tree": source.get("declared_target_tree"),
        "rule_ref": rule_ref,
        "profile_ref": profile_ref,
        "baseline_ref": baseline_ref,
        "baseline_source_ref": baseline_source_ref,
        "environment_inputs": env_inputs,
        "tool_identities": tool_identities,
        "prerequisite_inputs": prereq_inputs,
        "changed_paths_content_digest": changed_paths_content_digest,
        "overlay_subset_paths": list(overlay_subset_paths),
    }
    if verified_deleted:
        closure_payload["verified_deleted_scope"] = list(verified_deleted)
    proof = {
        "phase": "pre_execution",
        "check_name": check_name,
        "check_id": definition.check_id if definition is not None else None,
        "definition_version": (
            definition.definition_version if definition is not None else None
        ),
        "scope_id": bound_scope_id,
        "expected_check_ref": bound_expected_ref,
        "execution_context": execution_context,
        "execution_started": False,
        "trusted": False,
        "required": False,
        "status": "verified_candidate_inputs",
        "reason": None,
        "source_ref": source,
        "rule_ref": rule_ref,
        "rule_source_ref": rule_source_ref,
        "profile_ref": profile_ref,
        "baseline_ref": baseline_ref,
        "baseline_source_ref": baseline_source_ref,
        "environment_inputs": env_inputs,
        "tool_identities": tool_identities,
        "prerequisite_inputs": prereq_inputs,
        "changed_paths_content_digest": changed_paths_content_digest,
        "overlay_subset_paths": list(overlay_subset_paths),
        "closure_digest": _canonical_sha256(closure_payload),
    }
    if verified_deleted:
        proof["verified_deleted_scope"] = list(verified_deleted)
    return proof


def compare_pre_execution_to_historical_result(pre_proof, historical_result):
    """Compare a P0 pre-execution proof to one historical Native Result's inputs.

    Evaluates **only single-Result factual input equivalence**
    (``comparison_scope="single_native_result"``). It never checks multi-requirement
    completeness (all-required / after / alternatives), Witness composition,
    issuer trust, PASS/FAIL policy, or whole-Check skip/reuse eligibility.
    Callers evaluating Check satisfaction or cross-run reuse must combine
    per-Result facts with ``check_contract.evaluate_expected_satisfaction()`` and
    #63 T2 admission.
    """

    def mismatch(reason):
        return {
            "comparison_scope": "single_native_result",
            "equivalent": False,
            "single_result_inputs_equivalent": False,
            "matched_requirement_id": None,
            "whole_check_reuse_admitted": False,
            "check_satisfaction_evaluated": False,
            "reason": reason,
            "closure_digest": (
                pre_proof.get("closure_digest") if isinstance(pre_proof, dict) else None
            ),
            "trusted": False,
            "admission_status": "not_evaluated",
        }

    if (
        not isinstance(pre_proof, dict)
        or pre_proof.get("phase") != "pre_execution"
        or pre_proof.get("execution_started") is not False
        or pre_proof.get("status") != "verified_candidate_inputs"
    ):
        return mismatch("pre_execution_source_unverified")
    pre_src = pre_proof.get("source_ref")
    try:
        check_contract.validate_source_ref(pre_src)
    except check_contract.CheckContractError:
        return mismatch("pre_execution_source_unverified")
    if (
        pre_src["scope_kind"]
        not in {
            "check_input_closure",
            "full_input_closure",
        }
        or pre_src["binding_status"] != "content_digest"
    ):
        return mismatch("pre_execution_source_unverified")

    try:
        check_contract.validate_result(historical_result)
    except check_contract.CheckContractError:
        return mismatch("historical_result_invalid")

    hist_check = historical_result["check"]
    if hist_check.get("check_id") != pre_proof.get("check_id") or hist_check.get(
        "definition_version"
    ) != pre_proof.get("definition_version"):
        return mismatch("check_identity_mismatch")

    hist_src = historical_result["source_ref"]
    if hist_src.get("scope_kind") not in {
        "check_input_closure",
        "full_input_closure",
    } or hist_src.get("binding_status") not in {"content_digest", "byte_tree"}:
        return mismatch("historical_source_scope_insufficient")
    if hist_src.get("content_digest") != pre_src.get("content_digest") or hist_src.get(
        "verified_input_scope"
    ) != pre_src.get("verified_input_scope"):
        return mismatch("source_content_mismatch")
    if (
        pre_src.get("declared_target_tree") is not None
        and hist_src.get("declared_target_tree") is not None
        and hist_src["declared_target_tree"] != pre_src["declared_target_tree"]
    ):
        return mismatch("target_tree_mismatch")

    pre_base = pre_proof.get("baseline_ref")
    hist_base = historical_result.get("baseline_ref")
    if pre_base is not None or hist_base is not None:
        if not isinstance(pre_base, dict) or not isinstance(hist_base, dict):
            return mismatch("baseline_mismatch")
        for key in (
            "base_commit",
            "base_tree",
            "baseline_status",
            "ref_action",
            "head_commit",
        ):
            if hist_base.get(key) != pre_base.get(key):
                return mismatch("baseline_mismatch")

    if (
        pre_proof.get("rule_ref") is None
        or hist_check.get("rule_ref") != pre_proof["rule_ref"]
    ):
        return mismatch("rule_mismatch")

    if (
        pre_proof.get("profile_ref") is not None
        and hist_check.get("profile_ref") != pre_proof["profile_ref"]
    ):
        return mismatch("profile_mismatch")

    if pre_proof.get("check_name") == "whitespace":
        pre_env = pre_proof.get("environment_inputs")
        hist_env = (historical_result.get("evidence") or {}).get(
            "git_whitespace_config"
        )
        if (
            not isinstance(pre_env, dict)
            or not isinstance(hist_env, dict)
            or (hist_env != pre_env)
        ):
            return mismatch("whitespace_config_mismatch")

    pre_gen = [
        ref
        for entry in (pre_proof.get("prerequisite_inputs") or ())
        for ref in (entry.get("generated_input_refs") or ())
    ]
    hist_gen = [
        ref
        for ref in (historical_result.get("input_refs") or ())
        if isinstance(ref, dict) and ref.get("kind") == "generated_input"
    ]
    if sorted(
        pre_gen, key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"))
    ) != sorted(
        hist_gen, key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"))
    ):
        return mismatch("prerequisite_artifact_mismatch")

    definition = check_contract.definition_for(pre_proof.get("check_name"))
    tool_reqs = pre_proof.get("tool_identities")
    if isinstance(tool_reqs, list) and tool_reqs:
        result_tool = historical_result.get("tool")
        if not isinstance(result_tool, dict):
            return mismatch("tool_mismatch")
        matched_pre = False
        for req_dict in tool_reqs:
            for alt in req_dict.get("alternatives") or ():
                if (
                    result_tool.get("access_id") == alt.get("access_id")
                    and result_tool.get("authority") == alt.get("authority")
                    and result_tool.get("tool_id") == alt.get("tool_id")
                    and result_tool.get("tool_id_source") == alt.get("tool_id_source")
                    and not any(
                        alt.get(k) is not None and result_tool.get(k) != alt.get(k)
                        for k in ("version", "artifact_sha256")
                    )
                ):
                    matched_pre = True
                    break
            if matched_pre:
                break
        if not matched_pre:
            return mismatch("tool_mismatch")
    matched_req_id = None
    if definition is not None and definition.tool_requirements:
        active_reqs = check_contract._active_requirements(
            definition, rule=pre_proof.get("rule_ref")
        )
        matched_req_id = next(
            (
                req.requirement_id
                for req in active_reqs
                if check_contract._result_matches_requirement(req, historical_result)
            ),
            None,
        )
        if matched_req_id is None:
            return mismatch("tool_mismatch")

    return {
        "comparison_scope": "single_native_result",
        "equivalent": True,
        "single_result_inputs_equivalent": True,
        "matched_requirement_id": matched_req_id,
        "whole_check_reuse_admitted": False,
        "check_satisfaction_evaluated": False,
        "reason": None,
        "closure_digest": pre_proof.get("closure_digest"),
        "trusted": False,
        "admission_status": "not_evaluated",
    }


def _locate_expected_entry(
    expected_block,
    check_name,
    *,
    expected_check_ref=None,
    scope_id=None,
):
    if not isinstance(expected_block, dict):
        raise ExecutionError("source_resolution_expected_missing")
    per_ref = expected_block.get("per_ref")
    if isinstance(per_ref, list) and per_ref:
        candidates = []
        for ref_scope in per_ref:
            if not isinstance(ref_scope, dict):
                continue
            if scope_id is not None and ref_scope.get("scope_id") != scope_id:
                continue
            for entry in ref_scope.get("checks") or ():
                if (
                    expected_check_ref is not None
                    and entry.get("expected_check_ref") == expected_check_ref
                ):
                    return entry, ref_scope
                if expected_check_ref is None and entry.get("name") == check_name:
                    candidates.append((entry, ref_scope))
        if expected_check_ref is None:
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise ExecutionError("source_resolution_scope_required")
        raise ExecutionError("source_resolution_expected_missing")
    if scope_id is not None and expected_block.get("scope_id") != scope_id:
        raise ExecutionError("source_resolution_expected_missing")
    for entry in expected_block.get("checks") or ():
        if (
            expected_check_ref is not None
            and entry.get("expected_check_ref") == expected_check_ref
        ):
            return entry, expected_block
        if expected_check_ref is None and entry.get("name") == check_name:
            return entry, expected_block
    raise ExecutionError("source_resolution_expected_missing")


def _independent_rule_for_check(
    plan,
    check_name,
    *,
    root,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
):
    if check_name in _HYGIENE_CHECKS:
        cfg, cfg_path, raw_sha, reason = _load_verified_config(
            plan,
            root,
            hygiene_config,
            key="hygiene",
            default_name="ci-hygiene.toml",
            loader=hygiene.load_config,
            error_type=hygiene.HygieneError,
            identity_fn=hygiene.config_identity,
        )
        if reason is not None or cfg is None:
            raise ExecutionError(reason or "hygiene_config_unverified")
        rule_ref, cfg_input = _resolved_rule_and_input(
            hygiene.config_identity,
            hygiene.HygieneError,
            cfg_path,
            cfg,
            root,
            fallback_name="engineering-ci.hygiene",
            digest_after=raw_sha,
        )
        return (
            rule_ref,
            {
                "name": rule_ref["name"],
                "version": str(rule_ref["version"]),
                "path": cfg_input["path"],
                "sha256": raw_sha,
            },
            cfg_input,
        )
    if check_name == "source":
        cfg, cfg_path, raw_sha, reason = _load_verified_config(
            plan,
            root,
            sources_config,
            key="sources",
            default_name="ci-sources.toml",
            loader=sources.load_config,
            error_type=sources.SourcesError,
            identity_fn=sources.config_identity,
        )
        if reason is not None or cfg is None:
            raise ExecutionError(reason or "sources_config_unverified")
        rule_ref, cfg_input = _resolved_rule_and_input(
            sources.config_identity,
            sources.SourcesError,
            cfg_path,
            cfg,
            root,
            fallback_name="engineering-ci.sources",
            digest_after=raw_sha,
        )
        return (
            rule_ref,
            {
                "name": rule_ref["name"],
                "version": str(rule_ref["version"]),
                "path": cfg_input["path"],
                "sha256": raw_sha,
            },
            cfg_input,
        )
    sel_cfg, cfg_path, raw_sha, reason = _load_verified_config(
        plan,
        root,
        selection_config,
        key="config",
        default_name="ci-selection.toml",
        loader=selection.load_config,
        error_type=selection.SelectionError,
        identity_fn=selection.config_identity,
    )
    if reason is not None or sel_cfg is None:
        raise ExecutionError(reason or "selection_config_unverified")
    rel_path = _stored_rule_path(cfg_path, root)
    rule_ref = check_contract.rule_ref(
        "engineering-ci.selection",
        getattr(sel_cfg, "schema_version", 1),
        path=rel_path,
        digest=raw_sha,
    )
    if check_name in _WORKFLOW_SCRIPT_CHECKS:
        config_inputs = (plan.get("inputs") or {}).get("config") or {}
        rule_ref = workflow_analysis.effective_check_rule_ref(
            check_name,
            selection_rule_ref=rule_ref,
            selection_config=sel_cfg,
            root=root,
            admitted_external_configs=config_inputs.get("workflow_external_configs"),
        )
        if rule_ref is None:
            raise ExecutionError("workflow_config_unverified")
    cfg_input = {"kind": "config", "path": rel_path, "sha256": raw_sha}
    return (
        rule_ref,
        {
            "name": rule_ref["name"],
            "version": str(rule_ref["version"]),
            "path": rel_path,
            "sha256": raw_sha,
        },
        cfg_input,
    )


def build_source_resolution(
    plan,
    check_or_name,
    *,
    root=None,
    expected_check_ref=None,
    scope_id=None,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    execution_context="host",
    admitted_external_targets=None,
    workflow_source_authority=None,
    producer_artifact_handoff=None,
    producer_artifacts_dir=None,
    producer_artifact_authority=None,
    expected_run_ref=None,
    expected_attempt_ref=None,
    execution_context_identity=None,
):
    """Independently build a #66-C Source/Rule/Baseline resolution item from Plan + disk/ODB.

    Workflow/Shell Source Handoff and #86 Producer Artifact Handoff use
    independently verified authorities and retained bundle bytes. Resolution
    ignores Native Results and retains partial provenance when evidence only
    covers changed paths.
    """
    if not isinstance(plan, dict):
        raise ExecutionError("source_resolution_invalid")
    check_name = (
        check_or_name.get("name")
        if isinstance(check_or_name, dict)
        else str(check_or_name)
    )
    if not check_name:
        raise ExecutionError("source_resolution_invalid")
    git = plan.get("git") or {}
    resolved_root = Path(root if root is not None else git.get("repo_root") or "")
    if not str(resolved_root) or not resolved_root.exists():
        raise ExecutionError("source_resolution_root_missing")
    matched_expected, expected_scope = _locate_expected_entry(
        plan.get("expected_checks"),
        check_name,
        expected_check_ref=expected_check_ref,
        scope_id=scope_id,
    )
    if matched_expected.get("name") != check_name:
        raise ExecutionError("source_resolution_expected_mismatch")

    workflow_check = check_name in _WORKFLOW_SCRIPT_CHECKS
    artifact_auth = _workflow_source_authorizer(
        plan,
        producer_artifact_authority
        if producer_artifact_authority is not None
        else workflow_source_authority,
    )
    authority = _workflow_source_authorizer(
        plan,
        workflow_source_authority
        if workflow_source_authority is not None
        else producer_artifact_authority,
    )
    handoff_verified = verify_producer_artifact_handoff(
        plan,
        check_name,
        handoff=producer_artifact_handoff,
        producer_artifacts_dir=producer_artifacts_dir,
        authority=artifact_auth,
        expected_run_ref=expected_run_ref,
        expected_attempt_ref=expected_attempt_ref,
        execution_context_identity=execution_context_identity,
    )
    effective_execution_context = (
        "dagger_export"
        if (
            handoff_verified is not None
            and execution_context == "host"
            and isinstance(producer_artifact_handoff, dict)
            and producer_artifact_handoff.get("execution_context") == "dagger_export"
        )
        else execution_context
    )
    gen_refs = (
        list(handoff_verified["generated_input_refs"])
        if handoff_verified is not None
        else []
    )
    art_tuples = (
        list(handoff_verified["artifact_tuples"])
        if handoff_verified is not None
        else []
    )
    art_paths = (
        tuple(handoff_verified["artifact_paths"])
        if handoff_verified is not None
        else ()
    )

    temporary = None
    work_root = resolved_root
    eval_plan = plan
    view_hyg = hygiene_config
    view_src = sources_config
    view_sel = selection_config
    if art_tuples:
        temporary, work_root = build_consumer_view(resolved_root, art_tuples)
        eval_plan = _rebased_consumer_plan(plan, resolved_root)
        view_hyg = _rebased_consumer_arg_path(hygiene_config, resolved_root, work_root)
        view_src = _rebased_consumer_arg_path(sources_config, resolved_root, work_root)
        view_sel = _rebased_consumer_arg_path(
            selection_config, resolved_root, work_root
        )
    try:
        rule_ref, rule_source_ref, cfg_input = _independent_rule_for_check(
            eval_plan,
            check_name,
            root=work_root,
            hygiene_config=view_hyg,
            sources_config=view_src,
            selection_config=view_sel,
        )
        scope_item = next(
            (
                dict(entry)
                for entry in _selected_checks(eval_plan)
                if isinstance(entry, dict) and entry.get("name") == check_name
            ),
            {"name": check_name, "inputs": []},
        )
        # A serialized check scope cannot supply an observation of Git inputs.
        scope_item.pop("git_whitespace_comparison", None)
        if gen_refs:
            scope_item["generated_input_refs"] = gen_refs
            scope_item["generated_inputs_retained"] = True
        snapshots = _prepare_source_snapshots(
            eval_plan,
            [scope_item],
            root=work_root,
            execution_context=effective_execution_context,
            hygiene_config=view_hyg,
            sources_config=view_src,
            artifact_paths=art_paths,
            workflow_authority=authority,
            base_root=resolved_root if art_tuples else None,
        )
        input_refs = [cfg_input]
        whitespace_comparison = None
        if (
            check_name == "whitespace"
            and not gen_refs
            and _uses_exported_index_comparison(
                eval_plan, effective_execution_context, extra_paths=art_paths
            )
        ):
            whitespace_comparison = _resolve_exported_index_whitespace(
                work_root,
                eval_plan["git"],
                _changeset_from_plan(eval_plan, work_root).paths,
            )
            scope_item["git_whitespace_comparison"] = whitespace_comparison
            input_refs.append(whitespace_comparison)
        if check_name == eval_plan["selection"].get("framework", "unittest"):
            input_refs.extend(_test_target_inputs(eval_plan, matched_expected))
        for ref in gen_refs:
            input_refs.append(dict(ref))
        seen_changed = set()

        def add_changed_path(path):
            if isinstance(path, str) and path and path not in seen_changed:
                seen_changed.add(path)
                input_refs.append({"kind": "changed_path", "path": path})

        for path in scope_item.get("inputs") or ():
            add_changed_path(path)
        if workflow_check:
            bound, reason, _ = _verify_execution_binding(
                eval_plan,
                resolved_root if art_tuples else work_root,
                execution_context=effective_execution_context,
            )
            if not bound:
                raise ExecutionError(reason or "workflow_target_source_unverified")
            admitted = workflow_analysis._authorized_external_targets(
                admitted_external_targets,
                check_name,
                functools.partial(authority, "external_target") if authority else None,
            )
            sel_cfg, _, sel_err = _load_optional_selection_config(
                eval_plan, work_root, selection_config=view_sel
            )
            scope_cfg = (
                sel_cfg.check_scope
                if sel_err is None and sel_cfg is not None
                else selection.CheckScopeConfig()
            )
            declared = scope_item.get("inputs")
            is_workflow_family = check_name in {
                "actionlint",
                "workflow_shellcheck",
                "zizmor",
            }
            if handoff_verified is not None and (
                not isinstance(declared, (list, tuple))
                or (scope_item.get("status") == "unknown" and not declared)
            ):
                if is_workflow_family:
                    declared = workflow_analysis.discover_workflow_files(
                        work_root,
                        scope_cfg.workflow_prefixes,
                        admitted_external_targets=admitted,
                    )
                else:
                    declared = workflow_analysis.discover_shell_files(
                        work_root,
                        scope_cfg.shell_prefixes,
                        admitted_external_targets=admitted,
                    )
            matched_extras = []
            for p in art_paths:
                matched = (
                    workflow_analysis.is_workflow_file(p, scope_cfg.workflow_prefixes)
                    if is_workflow_family
                    else workflow_analysis.is_shell_file(p, scope_cfg.shell_prefixes)
                )
                if matched:
                    matched_extras.append(p)
            target_candidates = sorted({*(declared or ()), *matched_extras})
            for path in target_candidates:
                add_changed_path(path)
            validate_target = _workflow_target_validator(
                eval_plan,
                work_root,
                gen_refs,
                authority=authority,
                _consumer_source_root=resolved_root if art_tuples else None,
            )
            targets = []
            for path in target_candidates:
                target, reason = workflow_analysis._inspect_target(
                    work_root.resolve(), path, admitted
                )
                if reason is None:
                    reason = validate_target(target, check_name=check_name)
                if reason is not None:
                    raise ExecutionError(reason)
                targets.append(target)
                input_refs.append({"kind": "scan_target", **target})
            scope_item = {**scope_item, "target_sources": targets}
        source_ref = _verified_check_source_ref(
            eval_plan,
            check_name,
            scope_item,
            root=work_root,
            execution_context=effective_execution_context,
            source_snapshots=snapshots,
            hygiene_config=view_hyg,
            sources_config=view_src,
            artifact_paths=art_paths,
            base_root=resolved_root if art_tuples else None,
        )
        if source_ref is None:
            source_ref = _source_reference(eval_plan, effective_execution_context)
        if workflow_check:
            source_ref = _workflow_result_source(scope_item, source_ref)
        verified_deleted = _verified_deleted_scope(
            eval_plan,
            matched_expected.get("source_ref"),
            source_ref,
            snapshots,
            root=work_root,
            execution_context=effective_execution_context,
            verified_overlay_paths=art_paths,
        )
        receipt_payload = {
            "scope_id": expected_scope.get("scope_id"),
            "expected_check_ref": matched_expected["expected_check_ref"],
            "source_ref": source_ref,
            "rule_source_ref": rule_source_ref,
        }
        if verified_deleted:
            receipt_payload["verified_deleted_scope"] = list(verified_deleted)
        if workflow_check or gen_refs or whitespace_comparison is not None:
            receipt_payload["input_refs"] = input_refs
        producer_exec_ident = None
        if handoff_verified is not None:
            p_ident = handoff_verified.get("execution_identity")
            if isinstance(p_ident, dict):
                producer_exec_ident = {
                    "execution_ref": p_ident.get("execution_ref"),
                    "run_ref": p_ident.get("run_ref"),
                    "attempt_ref": p_ident.get("attempt_ref"),
                }
            receipt_payload["producer_artifact_handoff_id"] = handoff_verified[
                "handoff_id"
            ]
            if producer_exec_ident is not None:
                receipt_payload["producer_execution_identity"] = producer_exec_ident
        receipt_sha = hashlib.sha256(
            json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        baseline = _baseline_reference(eval_plan)
        resolved_profile = _profile_reference(
            eval_plan,
            root=resolved_root,
            execution_context=effective_execution_context,
        )
        if matched_expected.get("profile_ref") is not None and resolved_profile is None:
            raise ExecutionError("source_resolution_profile_unverified")
        item = {
            "expected_check_ref": matched_expected["expected_check_ref"],
            "rule_ref": rule_ref,
            "source_ref": source_ref,
            "baseline_ref": baseline,
            "input_refs": input_refs,
            "rule_source_ref": rule_source_ref,
            "source_receipt_ref": {
                "name": "independent-source-resolution",
                "version": "1",
                "sha256": receipt_sha,
            },
        }
        if verified_deleted:
            item["verified_deleted_scope"] = list(verified_deleted)
        if handoff_verified is not None:
            item["producer_artifact_handoff_id"] = handoff_verified["handoff_id"]
            if producer_exec_ident is not None:
                item["producer_execution_identity"] = producer_exec_ident
        if resolved_profile is not None:
            item["profile_ref"] = resolved_profile
        changed_binding = git.get("content_binding") or {}
        changed_digest = changed_binding.get("digest")
        if isinstance(changed_digest, str) and changed_digest:
            current_changed = selection.worktree_content_binding(
                resolved_root, git.get("changed_paths") or ()
            )
            if current_changed.get("digest") != changed_digest:
                raise ExecutionError("source_resolution_changed_paths_stale")
            if not current_changed.get("unverified_paths"):
                item["changed_paths_content_digest"] = changed_digest
        if baseline is not None:
            baseline_source_ref = _git_baseline_source_ref(
                resolved_root, baseline, base_ref=git.get("base_ref")
            )
            if baseline_source_ref is None:
                raise ExecutionError("source_resolution_baseline_unproven")
            item["baseline_source_ref"] = baseline_source_ref
        return item
    finally:
        if temporary is not None:
            temporary.cleanup()


def source_resolution_receipt(
    plan,
    check_or_name,
    *,
    root=None,
    expected_check_ref=None,
    scope_id=None,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    execution_context="host",
    admitted_external_targets=None,
    workflow_source_authority=None,
    producer_artifact_handoff=None,
    producer_artifacts_dir=None,
    producer_artifact_authority=None,
    expected_run_ref=None,
    expected_attempt_ref=None,
    execution_context_identity=None,
):
    """Independent #66-C resolution builder (alias to build_source_resolution; ignores NativeResult)."""
    return build_source_resolution(
        plan,
        check_or_name,
        root=root,
        expected_check_ref=expected_check_ref,
        scope_id=scope_id,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
        selection_config=selection_config,
        execution_context=execution_context,
        admitted_external_targets=admitted_external_targets,
        workflow_source_authority=workflow_source_authority,
        producer_artifact_handoff=producer_artifact_handoff,
        producer_artifacts_dir=producer_artifacts_dir,
        producer_artifact_authority=producer_artifact_authority,
        expected_run_ref=expected_run_ref,
        expected_attempt_ref=expected_attempt_ref,
        execution_context_identity=execution_context_identity,
    )


def evaluate_source_resolution(
    plan,
    check_or_name,
    *,
    root=None,
    expected_check_ref=None,
    scope_id=None,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    execution_context="host",
    admitted_external_targets=None,
    workflow_source_authority=None,
    producer_artifact_handoff=None,
    producer_artifacts_dir=None,
    producer_artifact_authority=None,
    expected_run_ref=None,
    expected_attempt_ref=None,
    execution_context_identity=None,
):
    """Resolve a Check for #66-C reconciliation, distinguishing unverified vs contradicted."""
    check_name = (
        check_or_name.get("name")
        if isinstance(check_or_name, dict)
        else str(check_or_name)
    )
    resolved_ref = expected_check_ref
    if resolved_ref is None and isinstance(plan, dict):
        try:
            matched, _ = _locate_expected_entry(
                plan.get("expected_checks"),
                check_name,
                expected_check_ref=expected_check_ref,
                scope_id=scope_id,
            )
            resolved_ref = matched.get("expected_check_ref")
        except ExecutionError:
            resolved_ref = None
    try:
        return build_source_resolution(
            plan,
            check_or_name,
            root=root,
            expected_check_ref=expected_check_ref,
            scope_id=scope_id,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            selection_config=selection_config,
            execution_context=execution_context,
            admitted_external_targets=admitted_external_targets,
            workflow_source_authority=workflow_source_authority,
            producer_artifact_handoff=producer_artifact_handoff,
            producer_artifacts_dir=producer_artifacts_dir,
            producer_artifact_authority=producer_artifact_authority,
            expected_run_ref=expected_run_ref,
            expected_attempt_ref=expected_attempt_ref,
            execution_context_identity=execution_context_identity,
        )
    except ExecutionError as error:
        return {
            "expected_check_ref": resolved_ref,
            "check_name": check_name,
            "resolution_status": _classify_resolution_error(error.code),
            "reason": error.code,
        }


def _input_refs(rule, item=None):
    refs = []
    cfg_ref = (item or {}).get("config_input_ref")
    if isinstance(cfg_ref, dict) and cfg_ref.get("path") and cfg_ref.get("sha256"):
        refs.append(
            {"kind": "config", "path": cfg_ref["path"], "sha256": cfg_ref["sha256"]}
        )
    elif isinstance(rule, dict) and rule.get("path") and rule.get("sha256"):
        refs.append({"kind": "config", "path": rule["path"], "sha256": rule["sha256"]})
    for path in (item or {}).get("inputs") or ():
        if isinstance(path, str) and path:
            refs.append({"kind": "changed_path", "path": path})
    comparison = (item or {}).get("git_whitespace_comparison")
    if comparison is not None:
        refs.append(comparison)
    return refs


def _check_evidence(item, name):
    evidence = {
        key: item[key]
        for key in ("stdout", "stderr", "findings", "counts", "failures", "observation")
        if item.get(key) is not None
    }
    if name in _WORKFLOW_SCRIPT_CHECKS:
        for key in (
            "coverage",
            "composite_mode",
            "independent_shellcheck_exit_code",
            "actionlint_exit_code",
            "per_script_clean_receipts_available",
            "shared_actionlint",
            "native_json",
            "native_truncated",
            "limitations",
            "input_paths",
            "target_sources",
            "target_source_problem",
            "admission",
        ):
            if key in item and item[key] is not None:
                evidence[key] = item[key]
    if name == "docs":
        for key in ("link_scope", "link_scope_note"):
            if isinstance(item.get(key), str):
                evidence[key] = item[key]
    if name == "whitespace" and isinstance(item.get("git_whitespace_config"), dict):
        evidence["git_whitespace_config"] = item["git_whitespace_config"]
    if not evidence and name == "forwarding":
        evidence = {
            "analysis": "forwarding",
            "status": item.get("status"),
            "reason": item.get("reason"),
        }
    return evidence or None


def _tool_observation(
    definition,
    access_id,
    *,
    executable=None,
    tool_id=None,
    tool_id_source=None,
    version=None,
    artifact_sha256=None,
):
    """Observed evidence for a declared entry; unknown identity stays unknown."""
    for requirement in definition.tool_requirements:
        for alternative in requirement.alternatives:
            if alternative.access_id == access_id:
                # The namespace comes from the declaration; a verified
                # ToolAsset identity and its source must be supplied by the
                # executor, never copied from the declaration.
                return check_contract.tool_ref(
                    access_id,
                    tool_id=tool_id,
                    tool_id_source=tool_id_source,
                    version=version,
                    artifact_sha256=artifact_sha256,
                    authority=alternative.authority,
                    executable=executable,
                )
    raise check_contract.CheckContractError("result_tool_undeclared")


def _observed_tool(definition, access_id, item):
    # No executable and no native exit means the entry did not run; do not
    # claim a tool observation for a not-executed or pre-execution result.
    if item.get("executable") is None and item.get("exit_code") is None:
        return None
    return _tool_observation(
        definition,
        access_id,
        executable=item.get("executable"),
        version=item.get("tool_version"),
    )


def _result_origin_and_runner(execution_context):
    if execution_context == "dagger_export":
        return "host", "execution:dagger_export", "dagger"
    if execution_context in check_contract.ORIGINS:
        return execution_context, f"execution:{execution_context}", None
    return "unknown", "execution:unknown", None


def _inventory_result(
    definition,
    item,
    *,
    name,
    source,
    rule,
    baseline,
    origin,
    run_instance,
    index,
    profile_ref=None,
    runner_ref=None,
    execution_context=None,
    parent_execution_context=None,
):
    """Native Result for the tracked-file inventory that actually ran."""
    inventory = item["inventory"]
    requirement = next(
        (
            entry
            for entry in definition.tool_requirements
            if entry.requirement_id == "inventory"
        ),
        None,
    )
    if requirement is None:
        raise check_contract.CheckContractError("result_tool_undeclared")
    access_id = requirement.alternatives[0].access_id
    status = "passed" if inventory.get("exit_code") == 0 else "failed"
    evidence = {
        "stdout": inventory.get("stdout", ""),
        "stderr": inventory.get("stderr", ""),
    }
    observation = inventory.get("observation")
    if isinstance(observation, dict):
        evidence["observation"] = observation
        if observation.get("execution_location") == "dagger_container":
            origin, runner_ref = "dagger", "execution:dagger"
    if parent_execution_context is not None:
        evidence["execution_context"] = execution_context
        evidence["parent_execution_context"] = parent_execution_context
    return check_contract.native_result(
        definition,
        source=source,
        status=status,
        native_exit_code=inventory.get("exit_code"),
        result_origin=origin,
        runner_ref=runner_ref or f"execution:{origin}",
        result_instance=f"{run_instance}:{origin}:{index}:{name}:{access_id}",
        input_refs=[],
        rule_ref=rule,
        profile_ref=profile_ref,
        tool=_tool_observation(definition, access_id, executable=inventory.get("tool")),
        evidence=evidence,
        baseline_ref=baseline,
        reason=None if status == "passed" else "git_inventory_failed",
    )


def _native_results(
    definition,
    item,
    *,
    name,
    source,
    rule,
    baseline,
    origin,
    run_instance,
    index,
    profile_ref=None,
    runner_ref=None,
    execution_context=None,
    parent_execution_context=None,
):
    inputs = _input_refs(rule, item)
    resolved_runner = runner_ref or f"execution:{origin}"

    def instance(access_id):
        return f"{run_instance}:{origin}:{index}:{name}:{access_id}"

    def with_context(evidence):
        if parent_execution_context is None:
            return evidence
        return {
            **(evidence or {}),
            "execution_context": execution_context,
            "parent_execution_context": parent_execution_context,
        }

    if name == "docs" and isinstance(item.get("inventory"), dict):
        requirements = {
            entry.requirement_id: entry for entry in definition.tool_requirements
        }
        inventory_requirement = requirements.get("inventory")
        links_requirement = requirements.get("links")
        if inventory_requirement is None or links_requirement is None:
            raise check_contract.CheckContractError("result_tool_undeclared")
        inventory_access = inventory_requirement.alternatives[0].access_id
        links_access = links_requirement.alternatives[0].access_id
        inventory_result = _inventory_result(
            definition,
            item,
            name=name,
            source=source,
            rule=rule,
            baseline=baseline,
            origin=origin,
            run_instance=run_instance,
            index=index,
            profile_ref=profile_ref,
            runner_ref=resolved_runner,
            execution_context=execution_context,
            parent_execution_context=parent_execution_context,
        )
        links_inputs = [
            {
                "kind": "check_result",
                "result_id": inventory_result["result_id"],
                "path": inventory_access,
            },
            *inputs,
            *item.get("generated_input_refs", ()),
        ]
        links_origin, links_runner = origin, resolved_runner
        observation = item.get("observation")
        if (
            isinstance(observation, dict)
            and observation.get("execution_location") == "dagger_container"
        ):
            links_origin, links_runner = "dagger", "execution:dagger"
        links_result = check_contract.native_result(
            definition,
            source=source,
            status=item.get("status", "not_executed"),
            native_exit_code=item.get("exit_code"),
            result_origin=links_origin,
            runner_ref=links_runner,
            result_instance=f"{run_instance}:{links_origin}:{index}:{name}:{links_access}",
            input_refs=links_inputs,
            rule_ref=rule,
            profile_ref=profile_ref,
            tool=_observed_tool(definition, links_access, item),
            evidence=with_context(_check_evidence(item, name)),
            baseline_ref=baseline,
            reason=item.get("reason"),
        )
        return [inventory_result, links_result]
    requirement = (
        definition.tool_requirements[0] if definition.tool_requirements else None
    )
    declared_access_ids = (
        {alt.access_id for alt in requirement.alternatives} if requirement else set()
    )
    requested_access = item.get("access_id")
    access_id = (
        requested_access
        if requested_access in declared_access_ids
        else (requirement.alternatives[0].access_id if requirement else name)
    )
    extra_inputs = list(item.get("generated_input_refs") or ())
    for path in item.get("input_paths") or ():
        ref = {"kind": "changed_path", "path": path}
        if ref not in inputs and ref not in extra_inputs:
            extra_inputs.append(ref)
    extra_inputs.extend(
        {"kind": "scan_target", **target} for target in item.get("target_sources", ())
    )
    native = check_contract.native_result(
        definition,
        source=source,
        status=item.get("native_status", item.get("status", "not_executed")),
        native_exit_code=item.get("exit_code"),
        result_origin=origin,
        runner_ref=resolved_runner,
        result_instance=instance(access_id),
        input_refs=[*inputs, *extra_inputs],
        rule_ref=rule,
        profile_ref=profile_ref,
        tool=_observed_tool(definition, access_id, item) if requirement else None,
        evidence=with_context(_check_evidence(item, name)),
        baseline_ref=baseline,
        reason=item.get("reason"),
    )
    return [native]


def _workflow_result_source(item, source):
    """Keep the Plan Source and its distinct per-Check scan inputs."""
    if item.get("target_source_problem"):
        return check_contract.source_ref(
            source["snapshot"],
            declared_target_tree=source.get("declared_target_tree"),
            actual_execution_source=source.get("actual_execution_source"),
            head_commit=source.get("head_commit"),
            index_tree=source.get("index_tree"),
        )
    suffixes = []
    if any(
        target["policy_scope"] == "event_external"
        for target in item.get("target_sources", ())
    ):
        suffixes.append("event_external")
    if item.get("generated_input_refs") and item.get("generated_inputs_retained"):
        suffixes.append("generated_inputs")
    if suffixes:
        return {
            **source,
            "actual_execution_source": (
                f"{source['actual_execution_source']}_with_{'_and_'.join(suffixes)}"
            ),
        }
    return source


def _check_source_content_binding(source_snapshots, check_name, source):
    """Project verified per-path hashes matching ``source`` for report checks."""
    if (
        not isinstance(source, dict)
        or source.get("binding_status") not in {"content_digest", "byte_tree"}
        or not source.get("verified_input_scope")
    ):
        return None
    binding = _snapshot_binding_for_check(source_snapshots, check_name)
    if (
        not isinstance(binding, dict)
        or binding.get("unverified_paths")
        or not isinstance(binding.get("paths"), dict)
    ):
        return None
    sub_digest = _subset_digest_from_binding(binding, source["verified_input_scope"])
    if sub_digest is None or sub_digest != source.get("content_digest"):
        return None
    return {
        "algorithm": "sha256-paths",
        "digest": sub_digest,
        "paths": {
            path: binding["paths"][path] for path in source["verified_input_scope"]
        },
    }


def _decorate_checks(
    plan,
    checks,
    *,
    execution_context,
    run_instance,
    root=None,
    profile_ref=None,
    source_snapshots=None,
    hygiene_config=None,
    sources_config=None,
    artifact_paths=(),
    base_root=None,
    index_offset=0,
):
    fallback_source = _source_reference(plan, execution_context)
    baseline = _baseline_reference(plan)
    origin, runner_ref, parent_ctx = _result_origin_and_runner(execution_context)
    profile_root = base_root if base_root is not None else root
    verified_profile = _profile_reference(
        plan,
        root=profile_root,
        execution_context=execution_context,
        profile_ref=profile_ref,
    )
    authorized_names = {
        entry.get("name") for entry in _selected_checks(plan) if isinstance(entry, dict)
    }
    for index, item in enumerate(checks, start=index_offset):
        name = item.get("name")
        item["execution_context"] = execution_context
        if "plan_authorized" not in item:
            # Authorization is a Plan fact: the Check was selected by the Plan.
            item["plan_authorized"] = name in authorized_names
        if parent_ctx is not None:
            item["parent_execution_context"] = parent_ctx
        definition = check_contract.definition_for(name)
        if definition is None:
            item["check_id"] = None
            item["check_contract"] = {
                "kind": check_contract.CHECK_CONTRACT_KIND,
                "schema_version": check_contract.CONTRACT_VERSION,
                "status": "identity_missing",
            }
            # Preserve the real native status: identity rejection invalidates
            # ECI consumption, but does not make an executed Check unexecuted.
            if item.get("status") == "passed":
                item["reason"] = "check_identity_missing"
            continue
        item["check_id"] = definition.check_id
        item["check_version"] = definition.definition_version
        source = (
            _verified_check_source_ref(
                plan,
                name,
                item,
                root=root,
                execution_context=execution_context,
                source_snapshots=source_snapshots,
                hygiene_config=hygiene_config,
                sources_config=sources_config,
                artifact_paths=artifact_paths,
                base_root=base_root,
            )
            if root is not None
            else None
        ) or fallback_source
        if name in _WORKFLOW_SCRIPT_CHECKS:
            source = _workflow_result_source(item, source)
        if item.get("execution_started") is False:
            # ``execution_started`` describes the Check's own verdict process
            # (for docs: the Lychee scan). A prerequisite tool that already ran
            # keeps its real Native Result; the scan itself is not fabricated.
            if isinstance(item.get("inventory"), dict):
                try:
                    inventory_result = _inventory_result(
                        definition,
                        item,
                        name=name,
                        source=source,
                        rule=item.get("rule_ref"),
                        baseline=baseline,
                        origin=origin,
                        run_instance=run_instance,
                        index=index,
                        profile_ref=verified_profile,
                        runner_ref=runner_ref,
                        execution_context=execution_context,
                        parent_execution_context=parent_ctx,
                    )
                    check_contract.validate_result(inventory_result)
                    satisfaction = check_contract.evaluate_check_satisfaction(
                        definition, [inventory_result]
                    )
                except check_contract.CheckContractError as error:
                    item["check_contract"] = {
                        "kind": check_contract.CHECK_CONTRACT_KIND,
                        "schema_version": check_contract.CONTRACT_VERSION,
                        "status": error.code,
                    }
                    continue
                item["native_result"] = inventory_result
                item["native_results"] = [inventory_result]
                item["native_satisfaction"] = satisfaction
                item["result_id"] = inventory_result["result_id"]
                item["result_origin"] = inventory_result["result_origin"]
                item["source_ref"] = source
                binding = _check_source_content_binding(source_snapshots, name, source)
                if binding is not None and "source_content_binding" not in item:
                    item["source_content_binding"] = binding
            item["check_contract"] = {
                "kind": check_contract.CHECK_CONTRACT_KIND,
                "schema_version": check_contract.CONTRACT_VERSION,
                "status": ("valid" if item.get("native_result") else "not_started"),
            }
            continue
        rule = item.get("rule_ref")
        result_item = item
        if (
            name == "whitespace"
            and execution_context == "host"
            and root is not None
            and item.get("git_whitespace_config") is None
            and not item.get("git_whitespace_config_drifted")
        ):
            changes = _changeset_from_plan(plan, root)
            ws_cfg, ws_err = _effective_git_whitespace_config(
                root,
                changes.paths,
                tree=changes.index_tree if changes.base_tree is not None else None,
            )
            if ws_err is None and ws_cfg is not None:
                result_item = {**item, "git_whitespace_config": ws_cfg}
        try:
            native_results = _native_results(
                definition,
                result_item,
                name=name,
                source=source,
                rule=rule,
                baseline=baseline,
                origin=origin,
                run_instance=run_instance,
                index=index,
                profile_ref=verified_profile,
                runner_ref=runner_ref,
                execution_context=execution_context,
                parent_execution_context=parent_ctx,
            )
            for native in native_results:
                check_contract.validate_result(native)
            satisfaction = check_contract.evaluate_check_satisfaction(
                definition, native_results
            )
        except check_contract.CheckContractError as error:
            item["check_contract"] = {
                "kind": check_contract.CHECK_CONTRACT_KIND,
                "schema_version": check_contract.CONTRACT_VERSION,
                "status": error.code,
            }
            # A native process may actually have run and reported PASS.
            # A contract error invalidates ECI consumption, not that execution
            # fact. The report is blocked below, without forging not_executed.
            continue
        item["check_contract"] = {
            "kind": check_contract.CHECK_CONTRACT_KIND,
            "schema_version": check_contract.CONTRACT_VERSION,
            "status": item.get("target_source_problem") or "valid",
        }
        primary = native_results[-1] if name == "docs" else native_results[0]
        item["native_result"] = primary
        item["native_results"] = native_results
        item["native_satisfaction"] = satisfaction
        item["result_id"] = primary["result_id"]
        item["result_origin"] = primary["result_origin"]
        item["source_ref"] = source
        binding = _check_source_content_binding(source_snapshots, name, source)
        if binding is not None and "source_content_binding" not in item:
            item["source_content_binding"] = binding
    return checks


def run_host_check(
    plan,
    item,
    *,
    root,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    policy_admission=None,
    runner=None,
    whitespace_runner=None,
    execution_context="host",
    run_instance=None,
    artifact_paths=(),
    generated_input_refs=(),
    shared_cache=None,
    profile_ref=None,
    admitted_external_targets=None,
    workflow_source_authority=None,
    _consumer_source_root=None,
    _consumer_generated_retained=None,
):
    """Run one selected Host check (hygiene, source or workflow/script) with contract decoration."""
    name = item.get("name")
    run_instance = run_instance or uuid.uuid4().hex
    retained = (
        bool(item.get("generated_inputs_retained"))
        if _consumer_generated_retained is None
        else bool(_consumer_generated_retained)
    )
    workflow_authority = _workflow_source_authorizer(plan, workflow_source_authority)
    if _consumer_source_root is not None:
        plan = _rebased_consumer_plan(plan, _consumer_source_root)
        hygiene_config = _rebased_consumer_arg_path(
            hygiene_config, _consumer_source_root, root
        )
        sources_config = _rebased_consumer_arg_path(
            sources_config, _consumer_source_root, root
        )
        selection_config = _rebased_consumer_arg_path(
            selection_config, _consumer_source_root, root
        )
    source_snapshots = _prepare_source_snapshots(
        plan,
        [item],
        root=root,
        execution_context=execution_context,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
        artifact_paths=artifact_paths,
        workflow_authority=workflow_authority,
        base_root=_consumer_source_root,
    )
    if name in _HYGIENE_CHECKS:
        results, _ = _run_hygiene_checks(
            plan,
            [item],
            root=root,
            hygiene_config=hygiene_config,
            runner=runner,
            execution_context=execution_context,
            whitespace_runner=whitespace_runner,
            extra_paths=artifact_paths,
            generated_input_refs=generated_input_refs,
            check_omitted=False,
        )
        raw = results or [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "hygiene_check_missing",
            }
        ]
    elif name == "source":
        result, _ = _run_source_check(
            plan, [item], root=root, sources_config=sources_config, runner=runner
        )
        raw = [result] if result is not None else []
    elif name in _WORKFLOW_SCRIPT_CHECKS:
        results, _ = _run_workflow_script_checks(
            plan,
            [item],
            root=root,
            runner=runner,
            selection_config=selection_config,
            policy_admission=policy_admission,
            extra_paths=artifact_paths,
            generated_input_refs=generated_input_refs,
            check_omitted=False,
            shared_cache=shared_cache,
            admitted_external_targets=admitted_external_targets,
            workflow_authority=workflow_authority,
            consumer_source_root=_consumer_source_root,
        )
        raw = results or [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "workflow_script_check_missing",
            }
        ]
    else:
        raw = [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "no native check execution wired",
            }
        ]
    if retained:
        for entry in raw:
            if isinstance(entry, dict):
                entry["generated_inputs_retained"] = True
    decorated = _decorate_checks(
        plan,
        raw,
        execution_context=execution_context,
        run_instance=run_instance,
        root=root,
        profile_ref=profile_ref,
        source_snapshots=source_snapshots,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
        artifact_paths=artifact_paths,
        base_root=_consumer_source_root,
    )
    return decorated[0]


def _blocked_consumer_entry(name, selected_status, reason, blocked_by):
    return {
        "name": name,
        "selected": selected_status,
        "status": "not_executed",
        "exit_code": None,
        "reason": reason,
        "execution_started": False,
        "execution_kind": "not_started",
        "blocked_by": blocked_by,
    }


def _execute_with_dependencies(
    plan,
    *,
    root,
    junit_path,
    hygiene_config,
    sources_config,
    selection_config=None,
    policy_admission=None,
    actions,
    execution_context,
    runner,
    run_instance,
    expected_tree,
    report,
    profile_ref=None,
    admitted_external_targets=None,
    workflow_source_authority=None,
    produced_artifacts_dir=None,
):
    chosen = plan["selection"]
    tests = tuple(chosen["tests"])
    framework = chosen.get("framework", "unittest")
    selected = _selected_checks(plan)
    prerequisites = list(chosen.get("prerequisites", []))
    selected_by_name = {item.get("name"): item for item in selected}
    incoming = pre_execution_edges(plan)
    prereq_cache = {}
    check_cache = {}
    script_cache = {}
    closure_records = []
    retention_metadata = {}

    with tempfile.TemporaryDirectory() as artifact_dir:
        artifact_root = Path(artifact_dir)

        def ensure_prereq(name):
            if name not in prereq_cache:
                producer_ref = {"kind": "prerequisite_action", "name": name}
                prereq_cache[name] = run_prerequisite_action(
                    name,
                    actions.get(name),
                    root=root,
                    runner=runner,
                    execution_context=execution_context,
                    artifact_root=producer_artifact_root(artifact_root, producer_ref),
                )
            return prereq_cache[name]

        def resolve_incoming(consumer_key):
            edges = incoming.get(consumer_key, ())
            block_reasons = []
            blocked_by = []
            data_failures = []
            files = []
            seen_paths = {}
            for edge in edges:
                p_ref = edge["producer_ref"]
                p_kind, p_name = p_ref["kind"], p_ref["name"]
                if (
                    edge["dependency_kind"] == "data"
                    and p_kind != "prerequisite_action"
                ):
                    reason = "data_check_producer_unsupported"
                    block_reasons.append(reason)
                    blocked_by.append({"producer_ref": p_ref, "reason": reason})
                    data_failures.append(
                        {
                            "producer_ref": p_ref,
                            "output_ref": edge.get("output_ref"),
                            "reason": reason,
                        }
                    )
                    closure_records.append(
                        {
                            "producer_ref": p_ref,
                            "consumer_ref": edge["consumer_ref"],
                            "output_ref": edge.get("output_ref"),
                            "verified": False,
                            "reason": reason,
                            "sha256": None,
                            "materialized_files": [],
                        }
                    )
                    continue
                if p_kind == "prerequisite_action":
                    producer_res = ensure_prereq(p_name)
                    producer_root = producer_artifact_root(artifact_root, p_ref)
                else:
                    producer_res = ensure_check(p_name)
                    producer_root = None
                ok, reason, closure = evaluate_edge(
                    edge, producer_res, artifact_root=producer_root
                )
                if closure is not None:
                    closure_records.append(closure)
                    if closure.get("verified"):
                        for obj in closure.get("materialized_objects") or ():
                            rel = obj["path"]
                            kind = obj["kind"]
                            if rel in seen_paths and seen_paths[rel] != str(
                                producer_root
                            ):
                                block_reasons.append("data_artifact_conflict")
                                blocked_by.append(
                                    {
                                        "producer_ref": p_ref,
                                        "reason": "data_artifact_conflict",
                                    }
                                )
                                data_failures.append(
                                    {
                                        "producer_ref": p_ref,
                                        "output_ref": edge.get("output_ref"),
                                        "reason": "data_artifact_conflict",
                                    }
                                )
                                continue
                            if any(
                                _artifact_paths_overlap(rel, existing)
                                and owner != str(producer_root)
                                for existing, owner in seen_paths.items()
                            ):
                                block_reasons.append("data_artifact_conflict")
                                blocked_by.append(
                                    {
                                        "producer_ref": p_ref,
                                        "reason": "data_artifact_conflict",
                                    }
                                )
                                data_failures.append(
                                    {
                                        "producer_ref": p_ref,
                                        "output_ref": edge.get("output_ref"),
                                        "reason": "data_artifact_conflict",
                                    }
                                )
                                continue
                            seen_paths[rel] = str(producer_root)
                            files.append((producer_root, rel, kind))
                if not ok:
                    block_reasons.append(reason)
                    blocked_by.append({"producer_ref": p_ref, "reason": reason})
                    if edge.get("dependency_kind") == "data":
                        data_failures.append(
                            {
                                "producer_ref": p_ref,
                                "output_ref": edge.get("output_ref"),
                                "reason": reason,
                            }
                        )
            if data_failures:
                if isinstance(consumer_key, tuple):
                    consumer_ref = {
                        "kind": consumer_key[0],
                        "name": consumer_key[1],
                    }
                elif isinstance(consumer_key, dict):
                    consumer_ref = dict(consumer_key)
                else:
                    consumer_ref = {"kind": str(consumer_key)}
                delivery = report.setdefault(
                    "artifact_delivery", {"status": "blocked", "consumers": []}
                )
                delivery["status"] = "blocked"
                delivery.setdefault("reason", data_failures[0]["reason"])
                delivery["consumers"].append(
                    {
                        "consumer_ref": consumer_ref,
                        "failures": data_failures,
                    }
                )
            return block_reasons[0] if block_reasons else None, blocked_by, files

        def ensure_check(name):
            if name in check_cache:
                return check_cache[name]
            item = dict(selected_by_name[name])
            block_reason, blocked_by, files = resolve_incoming(("check", name))
            generated_refs = generated_input_refs(
                closure_records, {"kind": "check", "name": name}
            )
            item["generated_input_refs"] = generated_refs
            if block_reason is not None:
                entry = _decorated_blocked_check(
                    plan,
                    name,
                    item.get("status"),
                    block_reason,
                    blocked_by,
                    execution_context=execution_context,
                    run_instance=run_instance,
                    root=root,
                    profile_ref=profile_ref,
                )
            elif files:
                retained_for_check = False
                if produced_artifacts_dir is not None:
                    relevant = [
                        rec
                        for rec in closure_records
                        if rec.get("verified")
                        and rec.get("consumer_ref") == {"kind": "check", "name": name}
                    ]
                    if relevant:
                        try:
                            retention_metadata.update(
                                retain_closure_artifacts(
                                    artifact_root,
                                    produced_artifacts_dir,
                                    relevant,
                                )
                            )
                            retained_for_check = True
                        except ExecutionError as error:
                            entry = _decorated_blocked_check(
                                plan,
                                name,
                                item.get("status"),
                                error.code,
                                [],
                                execution_context=execution_context,
                                run_instance=run_instance,
                                root=root,
                                profile_ref=profile_ref,
                            )
                            check_cache[name] = entry
                            return entry
                item["generated_inputs_retained"] = retained_for_check
                try:
                    temporary, consumer_root = build_consumer_view(root, files)
                except ExecutionError as error:
                    entry = _decorated_blocked_check(
                        plan,
                        name,
                        item.get("status"),
                        error.code,
                        [],
                        execution_context=execution_context,
                        run_instance=run_instance,
                        root=root,
                        profile_ref=profile_ref,
                    )
                    check_cache[name] = entry
                    return entry
                try:
                    entry = run_host_check(
                        plan,
                        item,
                        root=consumer_root,
                        hygiene_config=hygiene_config,
                        sources_config=sources_config,
                        selection_config=selection_config,
                        policy_admission=policy_admission,
                        runner=runner,
                        execution_context=execution_context,
                        run_instance=run_instance,
                        artifact_paths=artifact_target_paths(files),
                        generated_input_refs=generated_refs,
                        shared_cache=script_cache,
                        profile_ref=profile_ref,
                        admitted_external_targets=admitted_external_targets,
                        workflow_source_authority=workflow_source_authority,
                        _consumer_source_root=root,
                        _consumer_generated_retained=retained_for_check,
                    )
                finally:
                    temporary.cleanup()
            else:
                entry = run_host_check(
                    plan,
                    item,
                    root=root,
                    hygiene_config=hygiene_config,
                    sources_config=sources_config,
                    selection_config=selection_config,
                    policy_admission=policy_admission,
                    runner=runner,
                    execution_context=execution_context,
                    run_instance=run_instance,
                    shared_cache=script_cache,
                    profile_ref=profile_ref,
                    admitted_external_targets=admitted_external_targets,
                    workflow_source_authority=workflow_source_authority,
                )
            check_cache[name] = entry
            return entry

        for item in selected:
            ensure_check(item.get("name"))

        if ("execution_report", "artifacts") in incoming:
            report_block, report_blocked_by, _ = resolve_incoming(
                ("execution_report", "artifacts")
            )
            if report_block is not None:
                delivery = report.setdefault(
                    "artifact_delivery", {"status": "blocked", "consumers": []}
                )
                delivery["status"] = "blocked"
                delivery.setdefault("reason", report_block)
                if not any(
                    consumer.get("consumer_ref")
                    == {"kind": "execution_report", "name": "artifacts"}
                    for consumer in delivery["consumers"]
                ):
                    delivery["consumers"].append(
                        {
                            "consumer_ref": {
                                "kind": "execution_report",
                                "name": "artifacts",
                            },
                            "failures": [
                                {
                                    "producer_ref": item.get("producer_ref"),
                                    "reason": item.get("reason"),
                                }
                                for item in report_blocked_by
                            ],
                        }
                    )

        gate = target_test_gate(plan)
        test_block_reason, test_blocked_by, test_files = resolve_incoming("tests")
        if not tests:
            report["tests"] = {"status": "not_executed", "exit_code": 3}
        elif gate is not None:
            gate_reason, coverage_scope = gate
            if coverage_scope is not None:
                report["coverage"]["scope"] = coverage_scope
            report["tests"] = {
                "status": "not_executed",
                "exit_code": None,
                "reason": gate_reason,
            }
        elif test_block_reason is not None:
            report["tests"] = {
                "status": "not_executed",
                "exit_code": None,
                "reason": test_block_reason,
                "blocked_by": test_blocked_by,
            }
        else:
            native = {}
            temporary = None
            test_root = root
            artifact_env = artifact_root
            if test_files:
                temporary, test_root = build_consumer_view(root, test_files)
                artifact_env = test_root
            previous_env = os.environ.get("ENGINEERING_CI_ARTIFACT_DIR")
            os.environ["ENGINEERING_CI_ARTIFACT_DIR"] = str(artifact_env)
            test_expected_tree = (
                expected_tree
                if (
                    execution_context != "dagger_export"
                    and selection._resolve_git_dir(root) is not None
                )
                else None
            )
            try:
                exit_code = selection.run_tests(
                    tests,
                    root=test_root,
                    framework=framework,
                    src_root=chosen.get("src_root", "src"),
                    expected_tree=test_expected_tree,
                    index_tree=test_expected_tree
                    if plan["git"].get("snapshot") == "index"
                    else None,
                    index_file=plan["git"].get("index_file"),
                    junit_path=junit_path,
                    capture=native,
                )
            finally:
                if previous_env is None:
                    os.environ.pop("ENGINEERING_CI_ARTIFACT_DIR", None)
                else:
                    os.environ["ENGINEERING_CI_ARTIFACT_DIR"] = previous_env
                if temporary is not None:
                    temporary.cleanup()
            if native.get("source_refusal"):
                report["status"] = "not_executed"
                report["exit_code"] = 4
                report["reason"] = native["source_refusal"]
                report["tests"] = {
                    "status": "not_executed",
                    "exit_code": 4,
                    "source_refusal": native["source_refusal"],
                }
                return report
            report["native_stdout"] = native.get("stdout", "")
            report["native_stderr"] = native.get("stderr", "")
            tests_status = "passed" if exit_code == 0 else "failed"
            report["tests"] = {
                "status": tests_status,
                "exit_code": exit_code,
                "observation": native,
            }

    checks_out = [check_cache[item.get("name")] for item in selected]
    omitted = omitted_hygiene_checks(
        plan,
        selected,
        root=root,
        hygiene_config=hygiene_config,
    ) + omitted_workflow_script_checks(
        plan,
        selected,
        root=root,
        selection_config=selection_config,
    )
    if omitted:
        checks_out.extend(
            _decorate_checks(
                plan,
                omitted,
                execution_context=execution_context,
                run_instance=run_instance,
                root=root,
                profile_ref=profile_ref,
            )
        )
    report["checks"] = checks_out
    report["prerequisites"] = [
        prereq_cache[name]
        if name in prereq_cache
        else run_prerequisite_action(
            name,
            actions.get(name),
            root=root,
            runner=runner,
            execution_context=execution_context,
        )
        for name in prerequisites
    ]
    if closure_records:
        report["artifact_closure"] = closure_records
    if produced_artifacts_dir is not None:
        if closure_records:
            unretained = [
                rec
                for rec in closure_records
                if rec.get("verified")
                and _edge_retention_key(
                    rec["producer_ref"], rec.get("consumer_ref"), rec["output_ref"]
                )
                not in retention_metadata
            ]
            if unretained:
                retention_metadata.update(
                    retain_closure_artifacts(
                        artifact_root, produced_artifacts_dir, unretained
                    )
                )
        report["producer_artifact_handoff"] = build_producer_artifact_handoff(
            plan,
            closure_records=closure_records,
            prerequisite_results=report.get("prerequisites") or (),
            execution_identity=report.get("execution_identity"),
            execution_context=execution_context,
            retention_metadata=retention_metadata,
        )
    return None


def _decorated_blocked_check(
    plan,
    name,
    selected_status,
    reason,
    blocked_by,
    *,
    execution_context,
    run_instance,
    root=None,
    profile_ref=None,
):
    return _decorate_checks(
        plan,
        [_blocked_consumer_entry(name, selected_status, reason, blocked_by)],
        execution_context=execution_context,
        run_instance=run_instance,
        root=root,
        profile_ref=profile_ref,
    )[0]


def _test_target_inputs(plan, expected=None):
    targets = plan["selection"].get("tests") or ()
    refs = [{"kind": "test_target", "path": target} for target in targets]
    if expected is not None:
        refs.extend(
            ref
            for ref in expected.get("input_refs") or ()
            if ref.get("kind") == "changed_path" and ref.get("path") in targets
        )
    return refs


def test_input_binding(plan, *, root, execution_context, source_snapshots):
    """Bind a selected framework invocation to its rules and observed Source."""
    if not plan or source_snapshots is None:
        return None
    framework = plan["selection"].get("framework", "unittest")
    source = _verified_check_source_ref(
        plan,
        framework,
        {},
        root=root,
        execution_context=execution_context,
        source_snapshots=source_snapshots,
    )
    if source is None:
        return None
    try:
        rule, _, config = _independent_rule_for_check(plan, framework, root=root)
    except ExecutionError:
        return None
    expected = next(
        (
            item
            for item in (plan.get("expected_checks") or {}).get("checks", ())
            if item["name"] == framework
        ),
        None,
    )
    return {
        "source_ref": source,
        "rule_ref": rule,
        "baseline_ref": _baseline_reference(plan),
        "profile_ref": _profile_reference(
            plan, root=root, execution_context=execution_context
        ),
        "input_refs": [config, *_test_target_inputs(plan, expected)],
    }


def _finalize_report(report, *, plan, root, source_snapshots=None):
    tests = report.get("tests") or {}
    if tests.get("observation") and not tests.get("check_contract"):
        origin, _, parent = _result_origin_and_runner(report["execution_context"])
        impl, hashes = native_producer.implementation(
            "engineering-ci/host/tests",
            {
                "engineering_ci/execution.py": Path(__file__),
                "engineering_ci/selection.py": Path(selection.__file__),
            },
        )
        native_producer.test_check(
            tests,
            framework=report["framework"],
            targets=report["targets"],
            observation={**tests["observation"], "parent_execution_context": parent},
            source=native_producer.source_reference(
                report.get("plan"), actual="worktree"
            ),
            context=report["execution_identity"],
            impl=impl,
            origin=origin,
            artifact_prefix=None,
            generated_inputs=tests.get("generated_input_refs") or (),
            plan_authorized=bool(plan["selection"]["tests"]),
            binding=test_input_binding(
                plan,
                root=root,
                execution_context=report["execution_context"],
                source_snapshots=source_snapshots,
            ),
        )
        tests["implementation_sources"] = hashes
        report["checks"].append({"name": report["framework"], **tests})
    delivery = report.get("artifact_delivery")
    if isinstance(delivery, dict):
        for consumer in delivery.get("consumers", []):
            consumer["failures"] = sorted(
                consumer.get("failures", []),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
        delivery["consumers"] = sorted(
            delivery.get("consumers", []),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
        if delivery.get("consumers") and delivery["consumers"][0].get("failures"):
            delivery["reason"] = delivery["consumers"][0]["failures"][0].get("reason")
    tests_res = report.get("tests") or {}
    tests_failed = bool(report.get("targets") and tests_res.get("status") == "failed")
    hygiene_or_source_failed = any(
        item.get("status") == "failed" for item in report["checks"]
    )
    prereq_failed = any(
        item.get("status") == "failed" for item in report["prerequisites"]
    )
    unfinished = [
        item for item in report["checks"] if item.get("status") == "not_executed"
    ] + [
        item for item in report["prerequisites"] if item.get("status") == "not_executed"
    ]
    unresolved = [item for item in report["checks"] if item.get("status") == "unknown"]
    if report.get("targets") and tests_res.get("status") == "not_executed":
        unfinished.append(tests_res)
    invalid_contract = [
        item
        for item in report["checks"]
        if item.get("check_contract", {}).get("status") not in {"valid", "not_started"}
    ]
    artifact_blocked = [
        entry
        for entry in (report.get("artifact_closure") or [])
        if not entry.get("verified")
    ]
    delivery_blocked = (report.get("artifact_delivery") or {}).get(
        "status"
    ) == "blocked"
    unsatisfied_native = [
        item for item in report["checks"] if _unsatisfied_native(item)
    ]
    unauthorized = [
        item for item in report["checks"] if item.get("plan_authorized") is False
    ]
    if unauthorized:
        report["routing_conflicts"] = [
            {
                "check": item.get("name"),
                "reason": item.get("routing_conflict") or "unauthorized_check_executed",
                "selected": item.get("selected"),
                "status": item.get("status"),
            }
            for item in unauthorized
        ]
    if tests_failed:
        report["status"], report["reason"] = "failed", "tests_failed"
        report["exit_code"] = tests_res.get("exit_code") or 1
    elif hygiene_or_source_failed:
        report["status"] = "failed"
        report["exit_code"] = 1
        report["reason"] = "checks_failed"
    elif prereq_failed:
        report["status"] = "failed"
        report["exit_code"] = 1
        report["reason"] = "prerequisites_failed"
    elif unauthorized:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = (
            "configured_check_omitted_from_plan"
            if all(
                item.get("routing_conflict") == "configured_check_omitted_from_plan"
                for item in unauthorized
            )
            else "unauthorized_check_executed"
        )
    elif invalid_contract:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "check_contract_invalid"
    elif delivery_blocked:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = (report.get("artifact_delivery") or {}).get(
            "reason"
        ) or "data_artifact_conflict"
    elif unsatisfied_native:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = "native_satisfaction_incomplete"
    elif unfinished or artifact_blocked or unresolved:
        report["status"] = "blocked"
        report["exit_code"] = 5
        specific = (
            tests_res.get("reason")
            if tests_res.get("status") == "not_executed" and tests_res.get("reason")
            else next(
                (
                    entry.get("reason")
                    for entry in artifact_blocked
                    if entry.get("reason")
                ),
                None,
            )
            or next(
                (
                    item.get("reason")
                    for item in unfinished
                    if item.get("blocked_by") and item.get("reason")
                ),
                None,
            )
            or next(
                (item.get("reason") for item in unresolved if item.get("reason")), None
            )
        )
        report["reason"] = specific or "required_checks_not_executed"
    else:
        report["status"], report["reason"] = "passed", None
        report["exit_code"] = 0
    return report


def target_test_gate(plan):
    """Return blocked (reason, coverage_scope) before launching any test runner."""
    chosen = (plan or {}).get("selection") or {}
    tests = tuple(chosen.get("tests") or ())
    if not tests:
        return None
    graph = chosen.get("tach_graph_validation") or {}
    if (
        chosen.get("requires_tach_check")
        and not chosen.get("full_tests")
        and graph.get("status") != "passed"
    ):
        return "tach_graph_unvalidated", None
    if chosen.get("coverage_required"):
        return "coverage_evidence_required", "not_measured"
    return None


def test_requirement_failure(plan):
    """Return gate failure reason string or None (PR #90 Data/PASS integration helper)."""
    gate = target_test_gate(plan)
    return gate[0] if gate is not None else None


def _unsatisfied_native(check):
    """True when a claimed verdict contradicts the Check's native composition.

    A native PASS requires a fully satisfied composition. A not_applicable
    verdict is clean only when no native tool of that Check actually failed;
    a failed inventory cannot become proof that the Check did not apply.
    """
    decision = check.get("native_satisfaction") or {}
    if decision.get("status") != "unsatisfied":
        return False
    if check.get("status") == "passed":
        return True
    if check.get("status") == "not_applicable":
        return any(
            result.get("status") == "failed"
            for result in check.get("native_results") or ()
        )
    return False


def execute(
    plan,
    *,
    root,
    junit_path=None,
    hygiene_config=None,
    sources_config=None,
    selection_config=None,
    policy_admission=None,
    prerequisite_actions=None,
    execution_context="host",
    runner=None,
    whitespace_runner=None,
    run_instance=None,
    profile_ref=None,
    admitted_external_targets=None,
    workflow_source_authority=None,
    produced_artifacts_dir=None,
    run_ref=None,
    attempt_ref=None,
    check_names=None,
):
    """Execute a Plan with optional independently verified Workflow/Shell Source claims.

    ``workflow_source_authority`` belongs to the runtime that authenticates the
    claims. A serialized Plan or external path/digest dictionary cannot act as
    that authority.
    """
    try:
        if bool(run_ref) != bool(attempt_ref):
            raise ExecutionError("execution_run_attempt_pair_required")
        validate_plan(plan)
        if (
            plan["schema_version"] == plan_contract.PLAN_VERSION
            and prerequisite_actions is not None
            and prerequisite_actions != plan.get("prerequisite_actions", {})
        ):
            raise ExecutionError("dependency_action_override_mismatch")
    except ExecutionError as error:
        return plan_rejection(error)
    root = Path(root)
    chosen = plan["selection"]
    tests = tuple(chosen["tests"])
    framework = chosen.get("framework", "unittest")
    selected = _selected_checks(plan)
    prerequisites = list(chosen.get("prerequisites", []))
    if check_names is not None:
        # A host Job may execute a subset of the same immutable Plan. The
        # caller reconciles all Jobs against the original Expected scope.
        eligible = {item["name"] for item in selected}
        if tests:
            eligible.add(framework)
        requested = set(check_names)
        if (
            execution_context != "host"
            or prerequisites
            or (plan.get("dependency_contract") or {}).get("edges")
            or not requested
            or not requested.issubset(eligible)
        ):
            return plan_rejection(ExecutionError("independent_host_checks_invalid"))
        selected = [item for item in selected if item["name"] in requested]
        if framework not in requested:
            tests = ()
    run_instance = run_instance or uuid.uuid4().hex
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "check_contract": {
            "kind": check_contract.CHECK_CONTRACT_KIND,
            "schema_version": check_contract.CONTRACT_VERSION,
        },
        "dependency_contract": plan.get("dependency_contract"),
        "plan_declaration_consistent": True
        if plan["schema_version"] == plan_contract.PLAN_VERSION
        else None,
        # Contract validation does not verify actual config bytes or a #66
        # Source/Prerequisite receipt. Narrow Git checks below remain separate.
        "source_binding_verified": "unknown",
        "execution_context": execution_context,
        "execution_identity": {
            "execution_ref": run_instance
            if run_instance.startswith("execution:")
            else "execution:" + run_instance,
            "run_ref": run_ref or None,
            "attempt_ref": attempt_ref or None,
            "run_attempt_verification": "unverified" if run_ref else "not_supplied",
            "origin_execution_attributed": False,
        },
        "plan": {
            "generator": plan.get("generator"),
            "git": plan.get("git"),
        },
        "targets": list(tests),
        "framework": framework,
        "checks": [],
        "junit": str(junit_path) if junit_path else None,
        "status": "not_executed",
        "exit_code": 3,
        "reason": "no_selected_tests",
        "coverage": {
            "required": bool(chosen.get("coverage_required")),
            "satisfied": False,
            "scope": "unverified",
        },
    }
    if (
        not tests
        and not selected
        and not prerequisites
        and plan["git"].get("ref_action") != "delete"
    ):
        return report
    bound_ok, bind_reason, expected_tree = _verify_execution_binding(
        plan, root, execution_context=execution_context
    )
    if not bound_ok:
        report["status"] = "not_executed"
        report["exit_code"] = (
            4 if bind_reason != "multi_ref_requires_per_ref_execution" else 3
        )
        report["reason"] = bind_reason
        return report
    actions = _prerequisite_actions(plan, prerequisite_actions)
    if pre_execution_edges(plan):
        early = _execute_with_dependencies(
            plan,
            root=root,
            junit_path=junit_path,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            selection_config=selection_config,
            policy_admission=policy_admission,
            actions=actions,
            execution_context=execution_context,
            runner=runner,
            run_instance=run_instance,
            expected_tree=expected_tree,
            report=report,
            profile_ref=profile_ref,
            admitted_external_targets=admitted_external_targets,
            workflow_source_authority=workflow_source_authority,
            produced_artifacts_dir=produced_artifacts_dir,
        )
        if early is not None:
            return early
        return _finalize_report(report, plan=plan, root=root)

    gate = target_test_gate(plan) if tests else None
    if gate is not None:
        gate_reason, coverage_scope = gate
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = gate_reason
        if coverage_scope is not None:
            report["coverage"]["scope"] = coverage_scope
        return report

    workflow_authority = _workflow_source_authorizer(plan, workflow_source_authority)
    source_snapshots = _prepare_source_snapshots(
        plan,
        selected,
        root=root,
        execution_context=execution_context,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
        workflow_authority=workflow_authority,
    )
    hygiene_results, _ = _run_hygiene_checks(
        plan,
        selected,
        root=root,
        hygiene_config=hygiene_config,
        runner=runner,
        execution_context=execution_context,
        whitespace_runner=whitespace_runner,
        check_omitted=check_names is None,
    )
    source_result, _ = _run_source_check(
        plan,
        selected,
        root=root,
        sources_config=sources_config,
        runner=runner,
    )
    workflow_script_results, _ = _run_workflow_script_checks(
        plan,
        selected,
        root=root,
        runner=runner,
        selection_config=selection_config,
        policy_admission=policy_admission,
        check_omitted=check_names is None,
        admitted_external_targets=admitted_external_targets,
        workflow_authority=workflow_authority,
    )
    native_checks = [*hygiene_results, *workflow_script_results]
    if source_result is not None:
        native_checks.append(source_result)
    decorated_native = _decorate_checks(
        plan,
        native_checks,
        execution_context=execution_context,
        run_instance=run_instance,
        root=root,
        profile_ref=profile_ref,
        source_snapshots=source_snapshots,
        hygiene_config=hygiene_config,
        sources_config=sources_config,
    )

    exit_code = 0
    if tests:
        native = {}
        test_expected_tree = (
            expected_tree
            if (
                execution_context != "dagger_export"
                and selection._resolve_git_dir(root) is not None
            )
            else None
        )
        exit_code = selection.run_tests(
            tests,
            root=root,
            framework=framework,
            src_root=chosen.get("src_root", "src"),
            expected_tree=test_expected_tree,
            index_tree=test_expected_tree
            if plan["git"].get("snapshot") == "index"
            else None,
            index_file=plan["git"].get("index_file"),
            junit_path=junit_path,
            capture=native,
        )
        if native.get("source_refusal"):
            report["status"] = "not_executed"
            report["exit_code"] = 4
            report["reason"] = native["source_refusal"]
            report["tests"] = {
                "status": "not_executed",
                "exit_code": 4,
                "source_refusal": native["source_refusal"],
            }
            return report
        report["exit_code"] = exit_code
        report["native_stdout"] = native.get("stdout", "")
        report["native_stderr"] = native.get("stderr", "")
        tests_status = "passed" if exit_code == 0 else "failed"
        report["tests"] = {
            "status": tests_status,
            "exit_code": exit_code,
            "observation": native,
        }
    else:
        report["tests"] = {"status": "not_executed", "exit_code": 3}

    covered = {item["name"] for item in decorated_native}
    if tests and report.get("tests", {}).get("observation"):
        # A framework Check observes the existing selected-tests invocation.
        covered.add(framework)
    remaining = [
        {
            "name": item.get("name"),
            "selected": item.get("status"),
            "status": "not_executed",
            "reason": "no native check execution wired",
        }
        for item in selected
        if item.get("name") not in covered
    ]
    decorated_remaining = (
        _decorate_checks(
            plan,
            remaining,
            execution_context=execution_context,
            run_instance=run_instance,
            root=root,
            profile_ref=profile_ref,
            source_snapshots=source_snapshots,
            hygiene_config=hygiene_config,
            sources_config=sources_config,
            index_offset=len(decorated_native),
        )
        if remaining
        else []
    )
    report["checks"] = decorated_native + decorated_remaining
    prereq_results, _ = _run_prerequisites(
        prerequisites,
        actions,
        root=root,
        runner=runner,
        execution_context=execution_context,
    )
    report["prerequisites"] = prereq_results
    return _finalize_report(
        report, plan=plan, root=root, source_snapshots=source_snapshots
    )


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci execute",
        description=(
            "Execute the tests and selected native checks in a selection plan "
            "and report native results."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--junit", type=Path)
    parser.add_argument(
        "--hygiene-config",
        type=Path,
        help="Hygiene config used for selected docs/tach/vulture checks",
    )
    parser.add_argument(
        "--sources-config",
        type=Path,
        help="Sources config used when check_scope selects source",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except ExecutionError as error:
        parser.exit(2, f"execution rejected: {error.code}\n")
    report = execute(
        plan,
        root=args.root,
        junit_path=args.junit,
        hygiene_config=args.hygiene_config,
        sources_config=args.sources_config,
    )
    if args.json:
        _print(report)
    else:
        print(f"status: {report['status']}")
        print(f"targets: {' '.join(report['targets']) or '(none)'}")
        print(f"exit_code: {report['exit_code']}")
        if report["reason"]:
            print(f"reason: {report['reason']}")
    if report["status"] == "passed":
        return 0
    if report["status"] == "not_executed":
        return 3
    if report["status"] == "blocked":
        return 5
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
