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


class PlanError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def prerequisite_contract(names, actions, *, declaration, selected_by, reason=None):
    """Describe the existing action -> final Report relation, without inference."""
    edges = []
    for name in sorted(names):
        spec = actions.get(name)
        resolved = isinstance(spec, dict) and spec.get("type") in {
            "command",
            "git_commit_present",
        }
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
    if chosen.get("framework", "unittest") not in {"pytest", "unittest"}:
        raise PlanError("framework_invalid")
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
        resolved = isinstance(spec, dict) and spec.get("type") in {
            "command",
            "git_commit_present",
        }
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
