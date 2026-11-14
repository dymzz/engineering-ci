"""Versioned Selection/Plan dependency declarations, not an execution graph.

The current producer proves post-check policy aggregation only. Data and PASS
preconditions require the B consumer and are refused rather than ignored here.
"""

from __future__ import annotations

import re

PLAN_KIND = "engineering-ci-selection-plan"
PLAN_VERSION = 2
DEPENDENCY_VERSION = 1
SEMANTIC = "prerequisite_policy_aggregation"
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
        and spec["type"] in {"command", "git_commit_present"}
    )


def _string_list(value):
    return isinstance(value, list) and all(isinstance(s, str) and s for s in value)


def _validate_expected(expected, git, plan):
    from . import check_contract, expected_checks

    if not isinstance(expected, dict):
        raise PlanError("expected_checks_invalid")
    if expected.get("kind") != "engineering-ci-expected-checks":
        raise PlanError("expected_checks_invalid")
    if expected.get("schema_version") != 1:
        raise PlanError("expected_checks_schema_unsupported")
    if expected.get("status") not in {"complete", "partial", "unknown"}:
        raise PlanError("expected_status_invalid")
    if expected.get("status") == "complete":
        raise PlanError("expected_complete_unproven")
    if expected.get("consumer_completeness") != "not_claimed":
        raise PlanError("expected_consumer_completeness_unproven")
    if expected.get("projection_source") != "check_scope":
        raise PlanError("expected_projection_source_unsupported")
    check_scope = plan.get("check_scope") or (plan.get("selection") or {}).get(
        "check_scope"
    )
    if check_scope is None:
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
    if expected.get("conflicts"):
        raise PlanError("expected_conflicts_unresolved")
    if not checks and (
        expected.get("status") != "unknown"
        or expected.get("definitions_status") != "unknown"
    ):
        raise PlanError("expected_status_inconsistent")
    if missing and expected.get("definitions_status") == "requested_names_complete":
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
    for key in ("hygiene", "sources"):
        declared = (inputs or {}).get(key)
        if declared is not None and (
            not isinstance(declared, dict)
            or ("path" in declared and not isinstance(declared["path"], str))
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


def validate(value):
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
    if "expected_checks" in value:
        if version != PLAN_VERSION:
            raise PlanError("expected_checks_requires_plan_v2")
        _validate_expected(value["expected_checks"], git, value)
    contract = value.get("dependency_contract")
    if version == 1:
        if "dependency_contract" in value:
            raise PlanError("dependency_contract_requires_plan_v2")
        return value
    if not isinstance(contract, dict):
        raise PlanError("dependency_contract_missing")
    if (
        type(contract.get("schema_version")) is not int
        or contract["schema_version"] != DEPENDENCY_VERSION
    ):
        raise PlanError("dependency_schema_unsupported")
    semantics = contract.get("required_semantics")
    if not isinstance(semantics, list) or semantics != [SEMANTIC]:
        raise PlanError("dependency_semantics_unsupported")
    edges = contract.get("edges")
    if not isinstance(edges, list):
        raise PlanError("dependency_edges_invalid")
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
        declaration = edge.get("declaration_ref")
        if (
            edge.get("source_binding_ref") != "git"
            or not isinstance(declaration, dict)
            or not isinstance(declaration.get("path"), str)
            or not declaration["path"]
            or declaration.get("sha256") != generator.get("config_sha256")
            or not isinstance(declaration.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", declaration["sha256"])
            or declaration.get("section") != f"prerequisite_actions.{producer['name']}"
        ):
            raise PlanError("dependency_binding_invalid")
        if edge.get("execution_phase") != "post_check":
            raise PlanError("dependency_phase_unsupported")
        spec = actions.get(producer["name"])
        resolved = _supported_action(spec)
        if spec is not None and not resolved:
            raise PlanError("dependency_action_unsupported")
        if resolved:
            if spec["type"] == "command":
                argv = spec.get("argv")
                if (
                    not isinstance(argv, list)
                    or not argv
                    or any(not isinstance(a, str) or not a for a in argv)
                ):
                    raise PlanError("dependency_action_parameters_invalid")
            elif not isinstance(spec.get("commit"), str) or not spec["commit"]:
                raise PlanError("dependency_action_parameters_invalid")
        if edge.get("resolution") != (
            "resolved" if resolved else "unresolved"
        ) or edge.get("unresolved_reason") != (
            None if resolved else "action_parameters_missing"
        ):
            raise PlanError("dependency_resolution_invalid")
        found.append(producer["name"])
    if sorted(found) != sorted(names):
        raise PlanError("dependency_inventory_mismatch")
    return value
