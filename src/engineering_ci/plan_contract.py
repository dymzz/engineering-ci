"""Versioned Selection/Plan dependency declarations.

`dependency_contract.schema_version == 1` covers post-check policy aggregation only
(`policy_only`). `schema_version == 2` adds machine-consumable `pre_execution_pass`
and `data` dependency edges enforced consistently before affected consumers run.
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

PLAN_KIND = "engineering-ci-selection-plan"
PLAN_VERSION = 2
DEPENDENCY_VERSION = 1
SUPPORTED_DEPENDENCY_VERSIONS = frozenset({1, 2})
SEMANTIC = "prerequisite_policy_aggregation"
SUPPORTED_SEMANTICS = frozenset({SEMANTIC, "pre_execution_pass", "data_dependency"})
DEPENDENCY_KINDS = frozenset({"policy_only", "pre_execution_pass", "data"})
OUTPUT_REF_KINDS = frozenset({"file", "directory"})
_KIND_SEMANTIC = {
    "policy_only": SEMANTIC,
    "pre_execution_pass": "pre_execution_pass",
    "data": "data_dependency",
}
# selection._check_scope emits applicability states and the test Selection's
# status. Explicit full selection is currently represented as escalated.
CHECK_SCOPE_STATUSES = frozenset(
    {
        "selected",
        "unknown",
        "not_applicable",
        "targeted",
        "escalated",
        "no_tests",
        "no_changes",
    }
)
# CLI mode "pr" produces the execution identity "pr_merge", not "pr".
GIT_SNAPSHOTS = frozenset({"worktree", "index", "commit", "push", "pr_merge", "multi"})


class PlanError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _supported_action(spec):
    return (
        isinstance(spec, dict)
        and isinstance(spec.get("type"), str)
        and spec["type"] in {"command", "dagger_command", "git_commit_present"}
    )


def validate_dagger_command(spec):
    """An explicit source-only, digest-pinned native producer declaration."""
    if (
        not isinstance(spec, dict)
        or set(spec) != {"type", "argv", "image", "platform", "cache"}
        or spec.get("type") != "dagger_command"
        or not _string_list(spec.get("argv"))
        or not spec["argv"]
        or spec.get("cache") != "source"
        or spec.get("platform") != "linux/amd64"
        or not isinstance(spec.get("image"), str)
        or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", spec["image"])
    ):
        raise PlanError("dagger_command_parameters_invalid")


def _string_list(value):
    return isinstance(value, list) and all(isinstance(s, str) and s for s in value)


def _validate_expected(expected, git, plan, *, root=None, materialized_sources=None):
    from . import check_contract, expected_checks

    if not isinstance(expected, dict):
        raise PlanError("expected_checks_invalid")
    if expected.get("kind") != "engineering-ci-expected-checks":
        raise PlanError("expected_checks_invalid")
    schema_version = expected.get("schema_version")
    if schema_version not in {1, 2}:
        raise PlanError("expected_checks_schema_unsupported")
    if expected.get("status") not in {"complete", "partial", "unknown"}:
        raise PlanError("expected_status_invalid")
    projection_source = expected.get("projection_source")
    if schema_version == 1:
        if expected.get("status") == "complete":
            raise PlanError("expected_complete_unproven")
        if expected.get("consumer_completeness") != "not_claimed":
            raise PlanError("expected_consumer_completeness_unproven")
        if projection_source != "check_scope":
            raise PlanError("expected_projection_source_unsupported")
    else:
        if projection_source != "effective_profile":
            raise PlanError("expected_checks_schema_unsupported")
        if expected.get("consumer_completeness") not in {
            "expected_domain_complete",
            "incomplete_conflict",
            "incomplete_profile",
            "per_ref_container_only",
            "unknown",
        }:
            raise PlanError("expected_consumer_completeness_invalid")
        if expected.get("authority_status") not in {"verified", "unverified"}:
            raise PlanError("effective_profile_authority_untrusted")
        if expected.get("trusted_admission_status", "not_evaluated") != "not_evaluated":
            raise PlanError("expected_trusted_admission_unproven")
        if expected.get("observed_input_closure_status") not in {
            "not_observed",
            "observed",
            "complete",
        }:
            raise PlanError("expected_observed_closure_invalid")
        if "non_check_obligations" in expected and not isinstance(
            expected["non_check_obligations"], list
        ):
            raise PlanError("expected_non_check_obligations_invalid")

    check_scope = plan.get("check_scope") or (plan.get("selection") or {}).get(
        "check_scope"
    )
    if schema_version == 1 and check_scope is None:
        raise PlanError("expected_check_scope_missing")
    rule = expected.get("selection_rule_ref")
    generator = plan.get("generator")
    if (
        not isinstance(rule, dict)
        or rule.get("name") != "engineering-ci.selection"
        or not isinstance(rule.get("sha256"), str)
        or not isinstance(generator, dict)
        or not isinstance(generator.get("config_sha256"), str)
        or rule["sha256"] != f"sha256:{generator['config_sha256']}"
    ):
        raise PlanError("expected_selection_rule_mismatch")
    for key in ("scope_id", "expected_set_ref"):
        value = expected.get(key)
        if not isinstance(value, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", value
        ):
            raise PlanError(f"expected_{key}_invalid")
    checks = expected.get("checks")
    if not isinstance(checks, list):
        raise PlanError("expected_checks_invalid")
    refs = [item.get("expected_check_ref") for item in checks if isinstance(item, dict)]
    if len(refs) != len(set(refs)):
        raise PlanError("expected_duplicate")
    try:
        expected_checks.validate_projection(
            expected,
            git,
            check_scope,
            plan=plan,
            root=root,
            materialized_sources=materialized_sources,
        )
    except expected_checks.ExpectedError as error:
        raise PlanError(error.code) from None
    seen = set()
    for item in checks:
        if not isinstance(item, dict):
            raise PlanError("expected_check_invalid")
        if item.get("definition_status") not in {"resolved", "unknown"}:
            raise PlanError("expected_definition_status_invalid")
        definition = check_contract.definition_for(
            item.get("name"), item.get("definition_version")
        )
        check_id = item.get("check_id")
        if item.get("definition_status") == "resolved":
            if (
                definition is None
                or check_id != definition.check_id
                or item.get("definition_version") != definition.definition_version
            ):
                raise PlanError("expected_definition_mismatch")
        elif definition is not None or check_id is not None:
            raise PlanError("expected_definition_mismatch")
        if check_id is not None and (
            not isinstance(check_id, str)
            or not re.fullmatch(
                r"eci/[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+", check_id
            )
        ):
            raise PlanError("expected_check_id_invalid")
        ref = item.get("expected_check_ref")
        if not isinstance(ref, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", ref):
            raise PlanError("expected_check_ref_invalid")
        if ref in seen:
            raise PlanError("expected_duplicate")
        seen.add(ref)
        applicability = item.get("applicability")
        if not isinstance(applicability, dict) or not isinstance(
            applicability.get("status"), str
        ):
            raise PlanError("expected_applicability_invalid")
        if not isinstance(item.get("policy_status"), str):
            raise PlanError("expected_policy_invalid")
    missing = expected.get("missing")
    if not isinstance(missing, list) or any(
        not isinstance(item, str) for item in missing
    ):
        raise PlanError("expected_missing_invalid")
    conflicts = expected.get("conflicts") or []
    if schema_version == 1:
        if conflicts:
            raise PlanError("expected_conflicts_unresolved")
        if not checks and (
            expected.get("status") != "unknown"
            or expected.get("definitions_status") != "unknown"
        ):
            raise PlanError("expected_status_inconsistent")
        if missing and expected.get("definitions_status") == "requested_names_complete":
            raise PlanError("expected_status_inconsistent")
    else:
        if conflicts and (
            expected.get("status") == "complete"
            or expected.get("consumer_completeness") != "incomplete_conflict"
        ):
            raise PlanError("expected_status_inconsistent")
        if expected.get("status") == "complete" and (
            expected.get("consumer_completeness") != "expected_domain_complete"
            or expected.get("definitions_status") != "domain_complete"
            or expected.get("applicability_status") != "domain_complete"
            or missing
            or conflicts
            or not isinstance(
                (expected.get("effective_profile") or {}).get("declared_checks"), list
            )
            or not (expected.get("effective_profile") or {}).get("domain_complete")
            or any(
                item.get("rule_status") != "resolved"
                or item.get("definition_status") != "resolved"
                or item.get("applicability", {}).get("status")
                in {"unknown", "incomplete"}
                for item in checks
            )
        ):
            raise PlanError("expected_complete_unproven")
        if missing and expected.get("definitions_status") in {
            "requested_names_complete",
            "domain_complete",
        }:
            raise PlanError("expected_status_inconsistent")
    unknown_names = sorted(
        str(item.get("name"))
        for item in checks
        if item.get("definition_status") == "unknown"
    )
    if sorted(missing) != unknown_names:
        raise PlanError("expected_status_inconsistent")
    return expected


def _validate_consumer_fields(value, chosen, git):
    """Validate nested shapes before consumers dereference them or start tools."""
    for container in (value, chosen):
        if "check_scope" in container:
            scope = container["check_scope"]
            if not isinstance(scope, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not isinstance(item.get("status"), str)
                or not item["status"]
                for item in scope
            ):
                raise PlanError("check_scope_invalid")
            if any(item["status"] not in CHECK_SCOPE_STATUSES for item in scope):
                raise PlanError("check_scope_status_unsupported")
    if "checks" in chosen and not _string_list(chosen["checks"]):
        raise PlanError("checks_invalid")
    if "code_prefixes" in chosen and not _string_list(chosen["code_prefixes"]):
        raise PlanError("code_prefixes_invalid")
    if "source_prefixes" in chosen and not _string_list(chosen["source_prefixes"]):
        raise PlanError("source_prefixes_invalid")
    if "weaver_prefixes" in chosen and (
        not isinstance(chosen["weaver_prefixes"], dict)
        or any(
            not isinstance(k, str) or not _string_list(v)
            for k, v in chosen["weaver_prefixes"].items()
        )
    ):
        raise PlanError("weaver_prefixes_invalid")
    if not _string_list(chosen.get("prerequisites", [])):
        raise PlanError("dependency_prerequisites_invalid")
    if chosen.get("tach_graph_validation") is not None and not isinstance(
        chosen["tach_graph_validation"], dict
    ):
        raise PlanError("tach_graph_validation_invalid")
    if "src_root" in chosen and not isinstance(chosen["src_root"], str):
        raise PlanError("src_root_invalid")
    for key in (
        "snapshot",
        "execution_tree",
        "index_tree",
        "index_file",
        "head_commit",
        "base_commit",
        "source_tree_id",
        "tree",
        "merge_tree",
        "base_tip",
        "base_ref",
        "head_ref",
    ):
        if git.get(key) is not None and not isinstance(git[key], str):
            raise PlanError("git_invalid")
    if git.get("snapshot") is not None and git["snapshot"] not in GIT_SNAPSHOTS:
        raise PlanError("git_snapshot_unsupported")
    if (
        git.get("snapshot") == "index"
        and git.get("execution_tree")
        and git.get("index_tree")
        and git["execution_tree"] != git["index_tree"]
    ):
        raise PlanError("index_source_identity_mismatch")
    if "changed_paths" in git and not _string_list(git["changed_paths"]):
        raise PlanError("git_invalid")
    if "deleted_paths" in git:
        deleted = git["deleted_paths"]
        if not _string_list(deleted) or any(
            "\\" in path
            or "\0" in path
            or PureWindowsPath(path).drive
            or any(part in {"", ".", ".."} for part in path.split("/"))
            for path in deleted
        ):
            raise PlanError("git_deleted_paths_invalid")
    content = git.get("content_binding")
    if content is not None and (
        not isinstance(content, dict)
        or (
            content.get("digest") is not None and not isinstance(content["digest"], str)
        )
    ):
        raise PlanError("git_invalid")
    inputs = value.get("inputs")
    if inputs is not None and not isinstance(inputs, dict):
        raise PlanError("inputs_invalid")
    for key in ("config", "hygiene", "sources"):
        declared = (inputs or {}).get(key)
        if declared is not None:
            if not isinstance(declared, dict) or (
                "path" in declared and not isinstance(declared["path"], str)
            ):
                raise PlanError("inputs_invalid")
            if declared.get("sha256") is not None and not re.fullmatch(
                r"[0-9a-f]{64}", str(declared["sha256"])
            ):
                raise PlanError("inputs_invalid")
            if declared.get("schema_version") is not None and (
                type(declared["schema_version"]) is not int
            ):
                raise PlanError("inputs_invalid")
            eff = declared.get(
                "effective_config_ref"
                if key == "config"
                else "effective_check_config_ref"
            )
            if eff is not None and (
                not isinstance(eff, dict)
                or not isinstance(eff.get("name"), str)
                or not isinstance(eff.get("version"), str)
                or not isinstance(eff.get("sha256"), str)
            ):
                raise PlanError("inputs_invalid")
    binding = value.get("post_completion")
    if binding is not None:
        if not isinstance(binding, dict):
            raise PlanError("post_completion_invalid")
        for key in (
            "weaver_config",
            "otlp",
            "expectations",
            "registry",
            "matcher_config",
            "advice_policies",
        ):
            declared = binding.get(key)
            if declared is not None and (
                not isinstance(declared, dict)
                or ("path" in declared and not isinstance(declared["path"], str))
            ):
                raise PlanError("post_completion_invalid")
        manifest = (binding.get("registry") or {}).get("manifest")
        if manifest is not None and not isinstance(manifest, dict):
            raise PlanError("post_completion_invalid")


def prerequisite_contract(names, actions, *, declaration, selected_by, reason=None):
    """Describe the existing action -> final Report relation, without inference."""
    edges = []
    for name in sorted(names):
        spec = actions.get(name)
        resolved = _supported_action(spec)
        edges.append(
            {
                "producer_ref": {"kind": "prerequisite_action", "name": name},
                "consumer_ref": {"kind": "execution_report", "name": "prerequisites"},
                "dependency_kind": "policy_only",
                "requires_pass": False,
                "output_ref": None,
                "source_binding_ref": "git",
                "declaration_ref": {
                    **declaration,
                    "section": f"prerequisite_actions.{name}",
                },
                "selection": {"selected_by": selected_by, "escalation_reason": reason},
                "execution_phase": "post_check",
                "resolution": "resolved" if resolved else "unresolved",
                "unresolved_reason": None if resolved else "action_parameters_missing",
            }
        )
    return {
        "schema_version": DEPENDENCY_VERSION,
        "required_semantics": [SEMANTIC],
        "edges": edges,
    }


def graph_contract(edges, *, required_semantics=None):
    """Build a version 2 dependency_contract for policy, PASS, and data edges."""
    edge_list = list(edges)
    if required_semantics is None:
        present = {_KIND_SEMANTIC[e["dependency_kind"]] for e in edge_list}
        semantics = sorted(present) if present else [SEMANTIC]
    else:
        semantics = list(required_semantics)
    return {
        "schema_version": 2,
        "required_semantics": semantics,
        "edges": edge_list,
    }


def _selected_check_names(value, chosen):
    scope = value.get("check_scope", chosen.get("check_scope"))
    if isinstance(scope, list):
        return {
            item["name"]
            for item in scope
            if isinstance(item, dict)
            and item.get("status") in {"selected", "unknown"}
            and isinstance(item.get("name"), str)
        }
    checks = chosen.get("checks")
    return set(checks) if isinstance(checks, list) else set()


def _validate_declaration_ref(edge, generator, *, expected_section=None):
    declaration = edge.get("declaration_ref")
    if (
        edge.get("source_binding_ref") != "git"
        or not isinstance(declaration, dict)
        or not isinstance(declaration.get("path"), str)
        or not declaration["path"]
        or declaration.get("sha256") != generator.get("config_sha256")
        or not isinstance(declaration.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", declaration["sha256"])
        or not isinstance(declaration.get("section"), str)
        or not declaration["section"]
        or (expected_section is not None and declaration["section"] != expected_section)
    ):
        raise PlanError("dependency_binding_invalid")


def _validate_action(actions, name, edge):
    spec = actions.get(name)
    resolved = _supported_action(spec)
    if spec is not None and not resolved:
        raise PlanError("dependency_action_unsupported")
    if resolved:
        if spec["type"] in {"command", "dagger_command"}:
            argv = spec.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(a, str) or not a for a in argv)
            ):
                raise PlanError("dependency_action_parameters_invalid")
            if spec["type"] == "dagger_command":
                validate_dagger_command(spec)
                if edge.get("dependency_kind") == "policy_only":
                    raise PlanError("dagger_command_requires_pre_execution")
        elif not isinstance(spec.get("commit"), str) or not spec["commit"]:
            raise PlanError("dependency_action_parameters_invalid")
    if edge.get("resolution") != ("resolved" if resolved else "unresolved") or edge.get(
        "unresolved_reason"
    ) != (None if resolved else "action_parameters_missing"):
        raise PlanError("dependency_resolution_invalid")


def _validate_output_ref(output_ref):
    if not isinstance(output_ref, dict):
        raise PlanError("dependency_output_ref_invalid")
    if output_ref.get("kind") not in OUTPUT_REF_KINDS:
        raise PlanError("dependency_output_ref_invalid")
    rel = output_ref.get("path")
    if (
        not isinstance(rel, str)
        or not rel
        or rel.startswith("/")
        or "\\" in rel
        or ".." in Path(rel).parts
        or rel.endswith("/")
        or any(part in {"", "."} for part in rel.split("/"))
    ):
        raise PlanError("dependency_output_ref_invalid")
    digest = output_ref.get("sha256")
    if digest is not None and (
        not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise PlanError("dependency_output_ref_invalid")


def _node_key(ref):
    kind = ref.get("kind")
    if kind == "tests":
        return ("tests",)
    return (kind, ref.get("name"))


def _check_cycles(pre_edges):
    graph = {}
    for producer, consumer in pre_edges:
        graph.setdefault(producer, []).append(consumer)
    state = {}

    def visit(node):
        mark = state.get(node)
        if mark == 1:
            raise PlanError("dependency_cycle_detected")
        if mark == 2:
            return
        state[node] = 1
        for nxt in graph.get(node, ()):
            visit(nxt)
        state[node] = 2

    for node in list(graph):
        if node not in state:
            visit(node)


def _validate_dependency_v1(contract, names, actions, generator):
    semantics = contract.get("required_semantics")
    if not isinstance(semantics, list) or semantics != [SEMANTIC]:
        raise PlanError("dependency_semantics_unsupported")
    edges = contract.get("edges")
    if not isinstance(edges, list):
        raise PlanError("dependency_edges_invalid")
    found = []
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("dependency_kind") != "policy_only":
            raise PlanError("dependency_kind_unsupported")
        producer, consumer = edge.get("producer_ref"), edge.get("consumer_ref")
        if (
            not isinstance(producer, dict)
            or producer.get("kind") != "prerequisite_action"
            or producer.get("name") not in names
        ):
            raise PlanError("dependency_producer_invalid")
        if consumer != {"kind": "execution_report", "name": "prerequisites"}:
            raise PlanError("dependency_consumer_unsupported")
        if (
            edge.get("requires_pass") is not False
            or "output_ref" not in edge
            or edge["output_ref"] is not None
        ):
            raise PlanError("dependency_condition_unsupported")
        _validate_declaration_ref(
            edge, generator, expected_section=f"prerequisite_actions.{producer['name']}"
        )
        if edge.get("execution_phase") != "post_check":
            raise PlanError("dependency_phase_unsupported")
        _validate_action(actions, producer["name"], edge)
        found.append(producer["name"])
    if sorted(found) != sorted(names):
        raise PlanError("dependency_inventory_mismatch")


def _validate_dependency_v2(value, chosen, contract, names, actions, generator):
    semantics = contract.get("required_semantics")
    if (
        not isinstance(semantics, list)
        or not semantics
        or any(
            not isinstance(item, str) or item not in SUPPORTED_SEMANTICS
            for item in semantics
        )
        or len(set(semantics)) != len(semantics)
    ):
        raise PlanError("dependency_semantics_unsupported")
    edges = contract.get("edges")
    if not isinstance(edges, list):
        raise PlanError("dependency_edges_invalid")
    selected_checks = _selected_check_names(value, chosen)
    found_prereqs = set()
    pre_pairs = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise PlanError("dependency_edges_invalid")
        kind = edge.get("dependency_kind")
        if not isinstance(kind, str) or kind not in DEPENDENCY_KINDS:
            raise PlanError("dependency_kind_unsupported")
        if _KIND_SEMANTIC[kind] not in semantics:
            raise PlanError("dependency_semantics_unsupported")
        producer, consumer = edge.get("producer_ref"), edge.get("consumer_ref")
        if not isinstance(producer, dict):
            raise PlanError("dependency_producer_invalid")
        p_kind, p_name = producer.get("kind"), producer.get("name")
        if p_kind == "prerequisite_action":
            if p_name not in names:
                raise PlanError("dependency_producer_invalid")
            _validate_action(actions, p_name, edge)
            _validate_declaration_ref(
                edge, generator, expected_section=f"prerequisite_actions.{p_name}"
            )
            found_prereqs.add(p_name)
        elif p_kind == "check" and kind != "policy_only":
            if kind == "data":
                raise PlanError("dependency_producer_unsupported")
            if not isinstance(p_name, str) or p_name not in selected_checks:
                raise PlanError("dependency_producer_invalid")
            if (
                edge.get("resolution") != "resolved"
                or edge.get("unresolved_reason") is not None
            ):
                raise PlanError("dependency_resolution_invalid")
            _validate_declaration_ref(edge, generator)
        else:
            raise PlanError("dependency_producer_invalid")
        if not isinstance(consumer, dict):
            raise PlanError("dependency_consumer_unsupported")
        if kind == "policy_only":
            if consumer != {"kind": "execution_report", "name": "prerequisites"}:
                raise PlanError("dependency_consumer_unsupported")
            if (
                edge.get("requires_pass") is not False
                or "output_ref" not in edge
                or edge["output_ref"] is not None
            ):
                raise PlanError("dependency_condition_unsupported")
            if edge.get("execution_phase") != "post_check":
                raise PlanError("dependency_phase_unsupported")
            continue
        c_kind = consumer.get("kind")
        valid_check_consumer = (
            c_kind == "check"
            and isinstance(consumer.get("name"), str)
            and consumer["name"] in selected_checks
            and set(consumer) == {"kind", "name"}
        )
        valid_artifact_consumer = kind == "data" and consumer == {
            "kind": "execution_report",
            "name": "artifacts",
        }
        if not (
            (c_kind == "tests" and set(consumer) == {"kind"})
            or valid_check_consumer
            or valid_artifact_consumer
        ):
            raise PlanError("dependency_consumer_unsupported")
        if kind == "pre_execution_pass":
            if (
                edge.get("requires_pass") is not True
                or "output_ref" not in edge
                or edge["output_ref"] is not None
            ):
                raise PlanError("dependency_condition_unsupported")
        else:
            if type(edge.get("requires_pass")) is not bool:
                raise PlanError("dependency_condition_unsupported")
            _validate_output_ref(edge.get("output_ref"))
        if edge.get("execution_phase") != "pre_execution":
            raise PlanError("dependency_phase_unsupported")
        pre_pairs.append((_node_key(producer), _node_key(consumer)))
    if found_prereqs != set(names):
        raise PlanError("dependency_inventory_mismatch")
    _check_cycles(pre_pairs)


WORKFLOW_SCRIPT_CHECK_NAMES = frozenset(
    {"actionlint", "shellcheck", "workflow_shellcheck", "zizmor"}
)


def _plan_check_names(value, chosen):
    names = set()
    for item in chosen.get("checks") or ():
        if isinstance(item, str):
            names.add(item)
    for source in (chosen.get("check_scope"), value.get("check_scope")):
        for item in source or ():
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
    return names


def _validate_workflow_config_snapshot(value, chosen):
    if not (_plan_check_names(value, chosen) & WORKFLOW_SCRIPT_CHECK_NAMES):
        return
    inputs = value.get("inputs")
    config = inputs.get("config") if isinstance(inputs, dict) else None
    if not isinstance(config, dict) or "workflow_config_digests" not in config:
        raise PlanError("workflow_config_snapshot_missing")
    if not isinstance(config["workflow_config_digests"], dict):
        raise PlanError("workflow_config_snapshot_invalid")


def validate(value, *, root=None, materialized_sources=None):
    """Common fail-closed rules for CLI, direct Python and Dagger entry points."""
    if not isinstance(value, dict) or value.get("kind") != PLAN_KIND:
        raise PlanError("plan_invalid")
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, PLAN_VERSION}:
        raise PlanError("plan_schema_unsupported")
    chosen, git = value.get("selection"), value.get("git")
    if not isinstance(chosen, dict) or not isinstance(git, dict):
        raise PlanError("plan_invalid")
    tests = chosen.get("tests")
    if not isinstance(tests, list) or any(not isinstance(t, str) for t in tests):
        raise PlanError("tests_invalid")
    framework = chosen.get("framework", "unittest")
    if not isinstance(framework, str) or framework not in {"pytest", "unittest"}:
        raise PlanError("framework_invalid")
    _validate_consumer_fields(value, chosen, git)
    if version == PLAN_VERSION:
        _validate_workflow_config_snapshot(value, chosen)
    if "expected_checks" in value:
        if version != PLAN_VERSION:
            raise PlanError("expected_checks_requires_plan_v2")
        _validate_expected(
            value["expected_checks"],
            git,
            value,
            root=root,
            materialized_sources=materialized_sources,
        )
    contract = value.get("dependency_contract")
    if version == 1:
        if "dependency_contract" in value:
            raise PlanError("dependency_contract_requires_plan_v2")
        return value
    if not isinstance(contract, dict):
        raise PlanError("dependency_contract_missing")
    dep_version = contract.get("schema_version")
    if type(dep_version) is not int or dep_version not in SUPPORTED_DEPENDENCY_VERSIONS:
        raise PlanError("dependency_schema_unsupported")
    names = chosen.get("prerequisites", [])
    if (
        not isinstance(names, list)
        or any(not isinstance(n, str) or not n for n in names)
        or len(set(names)) != len(names)
    ):
        raise PlanError("dependency_prerequisites_invalid")
    actions = value.get("prerequisite_actions", {})
    if not isinstance(actions, dict):
        raise PlanError("dependency_actions_invalid")
    generator = value.get("generator")
    if not isinstance(generator, dict):
        raise PlanError("dependency_binding_invalid")
    if dep_version == 1:
        _validate_dependency_v1(contract, names, actions, generator)
    else:
        _validate_dependency_v2(value, chosen, contract, names, actions, generator)
    return value
