"""Execute a selection plan against its bound Git snapshot.

This is the host/Actions consumer of the plan. It never re-selects tests: it
runs exactly the plan targets, verifies the checkout tree for immutable
snapshots, and records native results separately from selection. Selected
Hygiene and source checks run even when no business tests were chosen.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from . import check_contract, hygiene, plan_contract, selection, sources

SCHEMA_VERSION = 1
REPORT_KIND = "engineering-ci-execution-report"
_HYGIENE_CHECKS = frozenset({"docs", "tach", "vulture", "whitespace", "forwarding"})


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


def _hygiene_config_path(plan, root, hygiene_config):
    return _config_path(
        plan, root, hygiene_config, key="hygiene", default_name="ci-hygiene.toml"
    )


def _sources_config_path(plan, root, sources_config):
    return _config_path(
        plan, root, sources_config, key="sources", default_name="ci-sources.toml"
    )


def _verify_execution_binding(plan, root):
    """Return (ok, reason, expected_tree). ok False means refuse execution."""
    git = plan["git"]
    expected_tree = git.get("execution_tree")
    snapshot = git.get("snapshot")
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
        expected_tree = expected_tree or index_tree
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
    if expected_tree:
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
    return True, None, expected_tree


def _changeset_from_plan(plan, root, extra_paths=()):
    git = plan["git"]
    paths = tuple(git.get("changed_paths") or ())
    complete = bool(git.get("base_commit")) and "changed_paths" in git
    return selection.ChangeSet(
        root=Path(root),
        base_ref=str(git.get("base_ref") or ""),
        head_ref=str(git.get("head_ref") or "HEAD"),
        base_commit=git.get("base_commit"),
        head_commit=git.get("head_commit"),
        tree=git.get("tree"),
        paths=paths,
        worktree_dirty=bool(git.get("worktree_dirty")),
        complete=complete,
        error=None if complete else "incomplete_plan_git",
        snapshot=str(git.get("snapshot") or "worktree"),
        index_tree=git.get("index_tree"),
        index_file=git.get("index_file"),
        merge_tree=git.get("merge_tree"),
        base_tip=git.get("base_tip"),
        generated_paths=tuple(extra_paths),
    )


def _run_hygiene_checks(
    plan,
    selected,
    *,
    root,
    hygiene_config,
    runner,
    extra_paths=(),
    generated_input_refs=(),
):
    selected_by_name = {
        item["name"]: item for item in selected if item.get("name") in _HYGIENE_CHECKS
    }
    wanted = tuple(selected_by_name)
    if not wanted:
        return [], False
    config_path = _hygiene_config_path(plan, root, hygiene_config)
    try:
        digest_before = check_contract.file_digest(config_path)
        config = hygiene.load_config(config_path)
        digest_after = check_contract.file_digest(config_path)
    except (OSError, hygiene.HygieneError) as error:
        code = getattr(error, "code", "read_failed")
        return [
            {
                "name": name,
                "selected": selected_by_name[name].get("status"),
                "status": "failed",
                "reason": f"hygiene_config_{code}",
                "execution_started": False,
                "execution_kind": "not_started",
            }
            for name in wanted
        ], True
    if digest_before != digest_after:
        return [
            {
                "name": name,
                "selected": selected_by_name[name].get("status"),
                "status": "failed",
                "reason": "hygiene_config_changed_during_read",
                "execution_started": False,
                "execution_kind": "not_started",
            }
            for name in wanted
        ], True
    rule = check_contract.rule_ref(
        "engineering-ci.hygiene",
        1,
        path=str(config_path),
        digest=digest_after,
    )
    checks = tuple(name for name in config.checks if name in selected_by_name)
    if not checks:
        checks = wanted
    report = hygiene.run(
        _changeset_from_plan(plan, root, extra_paths),
        dataclasses.replace(config, checks=checks),
        root=root,
        runner=runner or hygiene._run,
    )
    results = []
    seen = set()
    for native in report["checks"]:
        name = native["name"]
        base = name.removesuffix("_external")
        if base not in selected_by_name and name not in selected_by_name:
            continue
        payload = dict(native)
        payload["rule_ref"] = rule
        payload["generated_input_refs"] = list(generated_input_refs)
        if name == "docs":
            # The git inventory is a real native input step of this Check.
            payload["inventory"] = report.get("inventory")
        origin = selected_by_name.get(base) or selected_by_name.get(name)
        payload["selected"] = origin.get("status") if origin else "selected"
        results.append(payload)
        seen.add(name)
        seen.add(base)
    for name, item in selected_by_name.items():
        if name in seen:
            continue
        results.append(
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "hygiene_check_missing",
                "rule_ref": rule,
            }
        )
    return results, report["status"] == "failed"


def _run_source_check(plan, selected, *, root, sources_config, runner):
    item = next((entry for entry in selected if entry.get("name") == "source"), None)
    if item is None:
        return None, False
    config_path = _sources_config_path(plan, root, sources_config)
    try:
        digest_before = check_contract.file_digest(config_path)
        config = sources.load_config(config_path)
        digest_after = check_contract.file_digest(config_path)
    except (OSError, sources.SourcesError) as error:
        code = getattr(error, "code", "read_failed")
        return {
            "name": "source",
            "selected": item.get("status"),
            "status": "failed",
            "reason": f"sources_config_{code}",
            "execution_started": False,
            "execution_kind": "not_started",
        }, True
    if digest_before != digest_after:
        return {
            "name": "source",
            "selected": item.get("status"),
            "status": "failed",
            "reason": "sources_config_changed_during_read",
            "execution_started": False,
            "execution_kind": "not_started",
        }, True
    source_runner = runner or sources._run
    report = sources.reconcile(config, root=root, runner=source_runner)
    payload = {
        "name": "source",
        "selected": item.get("status"),
        "status": report.status,
        "reason": None if report.status == "passed" else "source_reconcile_failed",
        "counts": dict(report.counts),
        "failures": list(report.failures),
        "rule_ref": check_contract.rule_ref(
            "engineering-ci.sources",
            1,
            path=str(config_path),
            digest=digest_after,
        ),
    }
    return payload, report.status == "failed"


def _prerequisite_actions(plan, override):
    if override is not None:
        return dict(override)
    actions = plan.get("prerequisite_actions")
    if isinstance(actions, dict):
        return dict(actions)
    inputs = plan.get("inputs") or {}
    declared = inputs.get("prerequisite_actions") if isinstance(inputs, dict) else None
    return dict(declared) if isinstance(declared, dict) else {}


def test_requirement_failure(plan):
    """Existing test-only prerequisites shared by Host and native consumers."""
    chosen = plan["selection"]
    if not chosen["tests"]:
        return None
    graph = chosen.get("tach_graph_validation") or {}
    if (
        chosen.get("requires_tach_check")
        and not chosen.get("full_tests")
        and graph.get("status") != "passed"
    ):
        return "tach_graph_unvalidated"
    if chosen.get("coverage_required"):
        return "coverage_evidence_required"
    return None


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
        objects = [{"kind": "file", "path": rel}]
    else:
        if not target.is_dir():
            return {**base_result, "ok": False, "reason": "data_artifact_type_mismatch"}
        files = []
        for entry in sorted(
            target.rglob("*"), key=lambda item: item.relative_to(target).as_posix()
        ):
            if entry.is_symlink():
                return {**base_result, "ok": False, "reason": "data_artifact_symlink"}
            if entry.is_file():
                files.append(entry.relative_to(root_path).as_posix())
        digest = artifact_directory_id(target)
        objects = [{"kind": "directory", "path": rel}]
    if expected is not None and digest != expected:
        return {
            **base_result,
            "ok": False,
            "reason": "data_artifact_digest_mismatch",
            "sha256": digest,
            "files": files,
        }
    return {
        **base_result,
        "ok": True,
        "reason": None,
        "sha256": digest,
        "files": files,
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
    view = Path(temporary.name) / "source"
    shutil.copytree(source_root, view)
    for item in artifacts:
        if len(item) == 3:
            producer_root, rel, kind = item
        else:
            producer_root, rel = item
            kind = "file"
        if kind == "directory":
            shutil.copytree(
                Path(producer_root) / rel,
                view / rel,
                dirs_exist_ok=True,
            )
            continue
        target = view / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(producer_root) / rel, target)
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
            "materialized_objects": closure.get("materialized_objects", []),
        }
        if not closure["ok"]:
            return False, closure["reason"], entry
        return True, None, entry
    return True, None, None


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
        content_digest = binding.get("digest")
        paths = binding.get("paths")
        if isinstance(paths, dict):
            scope = sorted(str(path) for path in paths)
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
    # Relative names alone are not immutable comparison inputs. The native
    # whitespace/forwarding checks consume the resolved base_commit.
    if not git.get("base_commit"):
        return None
    ref = {"base_commit": git["base_commit"]}
    for key in ("base_ref", "base_tip"):
        if git.get(key) is not None:
            ref[key] = git[key]
    return ref


def _input_refs(rule):
    if isinstance(rule, dict) and rule.get("path") and rule.get("sha256"):
        return [{"kind": "config", "path": rule["path"], "sha256": rule["sha256"]}]
    return []


def _check_evidence(item, name):
    evidence = {
        key: item[key]
        for key in ("stdout", "stderr", "findings", "counts", "failures")
        if item.get(key) is not None
    }
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
    return _tool_observation(definition, access_id, executable=item.get("executable"))


def _inventory_result(
    definition, item, *, name, source, rule, baseline, origin, run_instance, index
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
    return check_contract.native_result(
        definition,
        source=source,
        status=status,
        native_exit_code=inventory.get("exit_code"),
        result_origin=origin,
        runner_ref=f"execution:{origin}",
        result_instance=f"{run_instance}:{origin}:{index}:{name}:{access_id}",
        input_refs=[],
        rule_ref=rule,
        tool=_tool_observation(definition, access_id, executable=inventory.get("tool")),
        evidence={
            "stdout": inventory.get("stdout", ""),
            "stderr": inventory.get("stderr", ""),
        },
        baseline_ref=baseline,
        reason=None if status == "passed" else "git_inventory_failed",
    )


def _native_results(
    definition, item, *, name, source, rule, baseline, origin, run_instance, index
):
    inputs = _input_refs(rule)

    def instance(access_id):
        return f"{run_instance}:{origin}:{index}:{name}:{access_id}"

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
        links_result = check_contract.native_result(
            definition,
            source=source,
            status=item.get("status", "not_executed"),
            native_exit_code=item.get("exit_code"),
            result_origin=origin,
            runner_ref=f"execution:{origin}",
            result_instance=instance(links_access),
            input_refs=links_inputs,
            rule_ref=rule,
            tool=_observed_tool(definition, links_access, item),
            evidence=_check_evidence(item, name),
            baseline_ref=baseline,
            reason=item.get("reason"),
        )
        return [inventory_result, links_result]
    requirement = (
        definition.tool_requirements[0] if definition.tool_requirements else None
    )
    access_id = requirement.alternatives[0].access_id if requirement else name
    native = check_contract.native_result(
        definition,
        source=source,
        status=item.get("status", "not_executed"),
        native_exit_code=item.get("exit_code"),
        result_origin=origin,
        runner_ref=f"execution:{origin}",
        result_instance=instance(access_id),
        input_refs=inputs,
        rule_ref=rule,
        tool=_observed_tool(definition, access_id, item) if requirement else None,
        evidence=_check_evidence(item, name),
        baseline_ref=baseline,
        reason=item.get("reason"),
    )
    return [native]


def _decorate_checks(plan, checks, *, execution_context, run_instance):
    source = _source_reference(plan, execution_context)
    baseline = _baseline_reference(plan)
    origin = (
        execution_context if execution_context in check_contract.ORIGINS else "unknown"
    )
    for index, item in enumerate(checks):
        name = item.get("name")
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
                item["result_origin"] = origin
                item["source_ref"] = source
            item["check_contract"] = {
                "kind": check_contract.CHECK_CONTRACT_KIND,
                "schema_version": check_contract.CONTRACT_VERSION,
                "status": ("valid" if item.get("native_result") else "not_started"),
            }
            continue
        rule = item.get("rule_ref")
        try:
            native_results = _native_results(
                definition,
                item,
                name=name,
                source=source,
                rule=rule,
                baseline=baseline,
                origin=origin,
                run_instance=run_instance,
                index=index,
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
            "status": "valid",
        }
        primary = native_results[-1] if name == "docs" else native_results[0]
        item["native_result"] = primary
        item["native_results"] = native_results
        item["native_satisfaction"] = satisfaction
        item["result_id"] = primary["result_id"]
        item["result_origin"] = origin
        item["source_ref"] = source
    return checks


def run_host_check(
    plan,
    item,
    *,
    root,
    hygiene_config=None,
    sources_config=None,
    runner=None,
    execution_context="host",
    run_instance=None,
    artifact_paths=(),
    generated_input_refs=(),
):
    """Run one selected Host check (hygiene or source) with contract decoration."""
    name = item.get("name")
    run_instance = run_instance or uuid.uuid4().hex
    if name in _HYGIENE_CHECKS:
        results, _ = _run_hygiene_checks(
            plan,
            [item],
            root=root,
            hygiene_config=hygiene_config,
            runner=runner,
            extra_paths=artifact_paths,
            generated_input_refs=generated_input_refs,
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
    else:
        raw = [
            {
                "name": name,
                "selected": item.get("status"),
                "status": "not_executed",
                "reason": "no native check execution wired",
            }
        ]
    decorated = _decorate_checks(
        plan, raw, execution_context=execution_context, run_instance=run_instance
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
    actions,
    execution_context,
    runner,
    run_instance,
    expected_tree,
    report,
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
    closure_records = []

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
                                continue
                            seen_paths[rel] = str(producer_root)
                            files.append((producer_root, rel, kind))
                if not ok:
                    block_reasons.append(reason)
                    blocked_by.append({"producer_ref": p_ref, "reason": reason})
            if block_reasons and any(
                edge.get("dependency_kind") == "data" for edge in edges
            ):
                report.setdefault(
                    "artifact_delivery",
                    {
                        "status": "blocked",
                        "reason": block_reasons[0],
                        "blocked_by": blocked_by,
                    },
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
                )
            elif files:
                temporary, consumer_root = build_consumer_view(root, files)
                try:
                    entry = run_host_check(
                        plan,
                        item,
                        root=consumer_root,
                        hygiene_config=hygiene_config,
                        sources_config=sources_config,
                        runner=runner,
                        execution_context=execution_context,
                        run_instance=run_instance,
                        artifact_paths=artifact_target_paths(files),
                        generated_input_refs=generated_refs,
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
                    runner=runner,
                    execution_context=execution_context,
                    run_instance=run_instance,
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
                report["artifact_delivery"] = {
                    "status": "blocked",
                    "reason": report_block,
                    "blocked_by": report_blocked_by,
                }

        requirement = test_requirement_failure(plan)
        test_block_reason, test_blocked_by, test_files = resolve_incoming("tests")
        if not tests:
            report["tests"] = {"status": "not_executed", "exit_code": 3}
        elif requirement:
            if requirement == "coverage_evidence_required":
                report["coverage"]["scope"] = "not_measured"
            report["tests"] = {
                "status": "not_executed",
                "exit_code": None,
                "reason": requirement,
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
            try:
                exit_code = selection.run_tests(
                    tests,
                    root=test_root,
                    framework=framework,
                    src_root=chosen.get("src_root", "src"),
                    expected_tree=expected_tree,
                    index_tree=expected_tree
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
            report["tests"] = {"status": tests_status, "exit_code": exit_code}

    report["checks"] = [check_cache[item.get("name")] for item in selected]
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
    return None


def _decorated_blocked_check(
    plan, name, selected_status, reason, blocked_by, *, execution_context, run_instance
):
    return _decorate_checks(
        plan,
        [_blocked_consumer_entry(name, selected_status, reason, blocked_by)],
        execution_context=execution_context,
        run_instance=run_instance,
    )[0]


def _finalize_report(report):
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
    elif unfinished or artifact_blocked:
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
        )
        report["reason"] = specific or "required_checks_not_executed"
    else:
        report["status"], report["reason"] = "passed", None
        report["exit_code"] = 0
    return report


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
    prerequisite_actions=None,
    execution_context="host",
    runner=None,
    run_instance=None,
):
    try:
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
        "plan": {
            "generator": plan.get("generator"),
            "git": plan.get("git"),
        },
        "targets": list(tests),
        "framework": framework,
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
    if not tests and not selected and not prerequisites:
        return report
    bound_ok, bind_reason, expected_tree = _verify_execution_binding(plan, root)
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
            actions=actions,
            execution_context=execution_context,
            runner=runner,
            run_instance=run_instance,
            expected_tree=expected_tree,
            report=report,
        )
        if early is not None:
            return early
        return _finalize_report(report)

    requirement = test_requirement_failure(plan)
    if requirement:
        report["status"] = "blocked"
        report["exit_code"] = 5
        report["reason"] = requirement
        if requirement == "coverage_evidence_required":
            report["coverage"]["scope"] = "not_measured"
        return report

    exit_code = 0
    if tests:
        native = {}
        exit_code = selection.run_tests(
            tests,
            root=root,
            framework=framework,
            src_root=chosen.get("src_root", "src"),
            expected_tree=expected_tree,
            index_tree=expected_tree
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
        report["tests"] = {"status": tests_status, "exit_code": exit_code}
    else:
        report["tests"] = {"status": "not_executed", "exit_code": 3}

    hygiene_results, _ = _run_hygiene_checks(
        plan,
        selected,
        root=root,
        hygiene_config=hygiene_config,
        runner=runner,
    )
    source_result, _ = _run_source_check(
        plan,
        selected,
        root=root,
        sources_config=sources_config,
        runner=runner,
    )
    native_checks = list(hygiene_results)
    if source_result is not None:
        native_checks.append(source_result)
    covered = {item["name"] for item in native_checks}
    covered.update(name.removesuffix("_external") for name in covered)
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
    report["checks"] = _decorate_checks(
        plan,
        native_checks + remaining,
        execution_context=execution_context,
        run_instance=run_instance,
    )
    prereq_results, _ = _run_prerequisites(
        prerequisites,
        actions,
        root=root,
        runner=runner,
        execution_context=execution_context,
    )
    report["prerequisites"] = prereq_results
    return _finalize_report(report)


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
