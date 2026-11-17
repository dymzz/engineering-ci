"""#61 G3/R3 Required Assessment server protection observation contract."""

from __future__ import annotations

import copy

import pytest

from engineering_ci import required_assessment_protection as rap
from engineering_ci.github_cli import ApiError
from engineering_ci.required_assessment_check import CHECK_NAME


class FakeGitHub:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path, method="GET", data=None):
        self.calls.append((method, path))
        assert method == "GET"
        if path not in self.routes:
            raise ApiError(500)
        status, payload = self.routes[path]
        if status != 200:
            raise ApiError(status)
        return copy.deepcopy(payload)


def empty_protection_routes(**extra):
    routes = {
        "rules/branches/main?per_page=100&page=1": (200, []),
        "rulesets?per_page=100&page=1": (200, []),
        "branches/main/protection": (404, None),
    }
    routes.update(extra)
    return routes


def test_inspect_absent_when_no_branch_rules_and_no_branch_protection():
    github = FakeGitHub(empty_protection_routes())
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["branch_rules"] == "readable"
    assert observation["visibility"]["branch_protection"] == "absent"
    assert observation["configuration"]["required_assessment_in_branch_rules"] is False
    assert (
        observation["configuration"]["required_assessment_in_branch_protection"]
        is False
    )
    assert observation["enforcement"]["status"] == "absent"
    assert observation["enforcement"]["probe"] == "not_performed"
    rap.validate_protection_observation(observation)


def test_inspect_configured_via_branch_applicable_rules():
    github = FakeGitHub(
        empty_protection_routes(
            **{
                "rules/branches/main?per_page=100&page=1": (
                    200,
                    [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [
                                    {"context": CHECK_NAME},
                                    {"context": "python-ci / checks"},
                                ]
                            },
                        }
                    ],
                )
            }
        )
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "configured"
    assert observation["configuration"]["required_assessment_in_branch_rules"] is True
    assert CHECK_NAME in observation["configuration"]["branch_rule_contexts"]
    rap.validate_protection_observation(observation)


def test_inspect_ignores_ruleset_that_does_not_apply_to_branch():
    # Supplemental Ruleset lists the Check for release/*, but effective branch
    # rules for main do not include it.
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 11, "name": "release-gate"}],
            ),
            "rulesets/11": (
                200,
                {
                    "id": 11,
                    "enforcement": "active",
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": CHECK_NAME}]
                            },
                        }
                    ],
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["configuration"]["required_assessment_in_branch_rules"] is False
    assert observation["enforcement"]["status"] == "absent"


def test_inspect_configured_via_branch_protection_contexts():
    github = FakeGitHub(
        empty_protection_routes(
            **{
                "branches/main/protection": (
                    200,
                    {
                        "required_status_checks": {
                            "strict": True,
                            "contexts": [CHECK_NAME],
                            "checks": [{"context": CHECK_NAME, "app_id": -1}],
                        }
                    },
                )
            }
        )
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["branch_protection"] == "readable"
    assert (
        observation["configuration"]["required_assessment_in_branch_protection"] is True
    )
    assert observation["enforcement"]["status"] == "configured"
    rap.validate_protection_observation(observation)


def test_inspect_not_verified_when_branch_rules_and_protection_forbidden():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (403, None),
            "rulesets?per_page=100&page=1": (403, None),
            "branches/main/protection": (403, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["branch_rules"] == "forbidden"
    assert observation["visibility"]["branch_protection"] == "forbidden"
    assert observation["configuration"]["required_assessment_in_branch_rules"] is None
    assert observation["enforcement"]["status"] == "not_verified"
    assert "no_readable_protection_source" in observation["enforcement"]["reasons"]
    rap.validate_protection_observation(observation)


def test_inspect_not_verified_when_only_partial_visibility():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (403, None),
            "rulesets?per_page=100&page=1": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "not_verified"
    assert "incomplete_protection_observation" in observation["enforcement"]["reasons"]


def test_inspect_not_verified_when_later_page_unread():
    page1 = [{"type": "deletion"} for _ in range(100)]
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, page1),
            # Missing page 2 fails closed; Check may live on a later page.
            "rulesets?per_page=100&page=1": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["configuration"]["branch_rules_complete"] is False
    assert observation["enforcement"]["status"] == "not_verified"
    assert observation["enforcement"]["status"] != "absent"
    assert any(
        reason in observation["enforcement"]["reasons"]
        for reason in (
            "branch_rules_pagination_incomplete",
            "branch_rules_read_error",
            "incomplete_protection_observation",
        )
    )


def test_inspect_configured_when_check_appears_on_later_page():
    page1 = [{"type": "deletion"} for _ in range(100)]
    page2 = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": CHECK_NAME}]},
        }
    ]
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, page1),
            "rules/branches/main?per_page=100&page=2": (200, page2),
            "rulesets?per_page=100&page=1": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["configuration"]["branch_rules_complete"] is True
    assert observation["enforcement"]["status"] == "configured"


def test_inspect_not_verified_when_ruleset_detail_absent_after_list():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 9, "name": "stale"}],
            ),
            "rulesets/9": (404, None),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    # Applicable sources complete without the Check => absent; detail race is
    # recorded but does not invent configuration.
    assert observation["enforcement"]["status"] == "absent"
    assert "ruleset_9_detail_absent" in observation["enforcement"]["reasons"]


def test_inspect_misconfigured_when_disabled_ruleset_applies_to_branch():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 5, "name": "disabled-main"}],
            ),
            "rulesets/5": (
                200,
                {
                    "id": 5,
                    "enforcement": "disabled",
                    "conditions": {
                        "ref_name": {
                            "include": ["refs/heads/main"],
                            "exclude": [],
                        }
                    },
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": CHECK_NAME}]
                            },
                        }
                    ],
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "misconfigured"
    assert (
        "required_assessment_context_only_in_disabled_ruleset"
        in observation["enforcement"]["reasons"]
    )
    rap.validate_protection_observation(observation)


def test_inspect_absent_when_disabled_ruleset_only_targets_release():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 5, "name": "disabled-release"}],
            ),
            "rulesets/5": (
                200,
                {
                    "id": 5,
                    "enforcement": "disabled",
                    "conditions": {
                        "ref_name": {
                            "include": ["refs/heads/release/v1"],
                            "exclude": [],
                        }
                    },
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": CHECK_NAME}]
                            },
                        }
                    ],
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "absent"
    assert observation["configuration"]["supplemental_ruleset_diagnostics"]
    assert (
        observation["configuration"]["supplemental_ruleset_diagnostics"][0][
            "branch_applicability"
        ]
        == "not_applicable"
    )


def test_inspect_misconfigured_when_evaluate_ruleset_applies_to_branch():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 6, "name": "evaluate-main"}],
            ),
            "rulesets/6": (
                200,
                {
                    "id": 6,
                    "enforcement": "evaluate",
                    "conditions": {
                        "ref_name": {
                            "include": ["refs/heads/main"],
                            "exclude": [],
                        }
                    },
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": CHECK_NAME}]
                            },
                        }
                    ],
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "misconfigured"
    assert (
        "required_assessment_context_only_in_evaluate_ruleset"
        in observation["enforcement"]["reasons"]
    )


def test_unknown_enforcement_does_not_count_as_active_applicable():
    github = FakeGitHub(
        {
            "rules/branches/main?per_page=100&page=1": (200, []),
            "rulesets?per_page=100&page=1": (
                200,
                [{"id": 7, "name": "unknown"}],
            ),
            "rulesets/7": (
                200,
                {
                    "id": 7,
                    "enforcement": None,
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [{"context": CHECK_NAME}]
                            },
                        }
                    ],
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "absent"
    assert "ruleset_7_enforcement_unknown" in observation["enforcement"]["reasons"]


def test_validate_rejects_tampered_observation_ref():
    github = FakeGitHub(empty_protection_routes())
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    observation["observation_ref"] = "sha256:" + ("0" * 64)
    with pytest.raises(rap.RequiredAssessmentProtectionError) as raised:
        rap.validate_protection_observation(observation)
    assert raised.value.code == "protection_observation_ref_mismatch"


def test_inspect_never_mutates_protection():
    github = FakeGitHub(empty_protection_routes())
    rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert all(method == "GET" for method, _path in github.calls)
