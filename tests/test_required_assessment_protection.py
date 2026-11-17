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


def test_inspect_absent_when_no_rulesets_and_no_branch_protection():
    github = FakeGitHub(
        {
            "rulesets": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["rulesets"] == "readable"
    assert observation["visibility"]["branch_protection"] == "absent"
    assert observation["configuration"]["required_assessment_in_rulesets"] is False
    assert (
        observation["configuration"]["required_assessment_in_branch_protection"]
        is False
    )
    assert observation["enforcement"]["status"] == "absent"
    assert observation["enforcement"]["probe"] == "not_performed"
    rap.validate_protection_observation(observation)


def test_inspect_configured_via_active_ruleset():
    github = FakeGitHub(
        {
            "rulesets": (200, [{"id": 11, "name": "main-gate"}]),
            "rulesets/11": (
                200,
                {
                    "id": 11,
                    "enforcement": "active",
                    "rules": [
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
                },
            ),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "configured"
    assert observation["configuration"]["required_assessment_in_rulesets"] is True
    assert CHECK_NAME in observation["configuration"]["ruleset_contexts"]
    rap.validate_protection_observation(observation)


def test_inspect_configured_via_branch_protection_contexts():
    github = FakeGitHub(
        {
            "rulesets": (200, []),
            "branches/main/protection": (
                200,
                {
                    "required_status_checks": {
                        "strict": True,
                        "contexts": [CHECK_NAME],
                        "checks": [{"context": CHECK_NAME, "app_id": -1}],
                    }
                },
            ),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["branch_protection"] == "readable"
    assert (
        observation["configuration"]["required_assessment_in_branch_protection"] is True
    )
    assert observation["enforcement"]["status"] == "configured"
    rap.validate_protection_observation(observation)


def test_inspect_not_verified_when_rulesets_forbidden():
    github = FakeGitHub(
        {
            "rulesets": (403, None),
            "branches/main/protection": (403, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["visibility"]["rulesets"] == "forbidden"
    assert observation["visibility"]["branch_protection"] == "forbidden"
    assert observation["configuration"]["required_assessment_in_rulesets"] is None
    assert (
        observation["configuration"]["required_assessment_in_branch_protection"] is None
    )
    assert observation["enforcement"]["status"] == "not_verified"
    assert "rulesets_not_readable" in observation["enforcement"]["reasons"]
    rap.validate_protection_observation(observation)


def test_inspect_not_verified_when_only_partial_visibility():
    github = FakeGitHub(
        {
            "rulesets": (403, None),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert observation["enforcement"]["status"] == "not_verified"
    assert (
        "partial_visibility_without_configured_context"
        in (observation["enforcement"]["reasons"])
    )


def test_inspect_misconfigured_when_only_disabled_ruleset_lists_context():
    github = FakeGitHub(
        {
            "rulesets": (200, [{"id": 5, "name": "disabled-gate"}]),
            "rulesets/5": (
                200,
                {
                    "id": 5,
                    "enforcement": "disabled",
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
    assert observation["configuration"]["required_assessment_in_rulesets"] is False
    assert observation["enforcement"]["status"] == "misconfigured"
    assert (
        "required_assessment_context_only_in_disabled_ruleset"
        in (observation["enforcement"]["reasons"])
    )
    rap.validate_protection_observation(observation)


def test_validate_rejects_tampered_observation_ref():
    github = FakeGitHub(
        {
            "rulesets": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    observation = rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    observation["observation_ref"] = "sha256:" + ("0" * 64)
    with pytest.raises(rap.RequiredAssessmentProtectionError) as raised:
        rap.validate_protection_observation(observation)
    assert raised.value.code == "protection_observation_ref_mismatch"


def test_inspect_never_mutates_protection():
    github = FakeGitHub(
        {
            "rulesets": (200, []),
            "branches/main/protection": (404, None),
        }
    )
    rap.inspect_server_protection("dymzz/engineering-ci", github=github)
    assert all(method == "GET" for method, _path in github.calls)
    assert not any(method != "GET" for method, _path in github.calls)
