"""Stable Check identity and Native Result contract tests."""

import pytest

from engineering_ci import check_contract


def source(tree="a" * 40):
    return check_contract.source_ref("worktree", tree=tree, head_commit="b" * 40)


def result(
    name="docs", *, instance="job-a", origin="host", status="passed", source_value=None
):
    definition = check_contract.definition_for(name)
    assert definition is not None
    return check_contract.native_result(
        definition,
        source=source_value or source(),
        status=status,
        result_origin=origin,
        result_instance=instance,
        native_exit_code=0 if status == "passed" else 1,
    )


def test_same_check_keeps_identity_but_results_are_distinct():
    first = result(instance="pre-commit")
    second = result(instance="pre-push")
    assert first["check"]["check_id"] == second["check"]["check_id"]
    assert first["result_id"] != second["result_id"]
    assert check_contract.validate_result_set([first, second])


def test_one_job_can_cover_multiple_checks():
    docs = result("docs", instance="job-1")
    tach = result("tach", instance="job-1")
    assert docs["runner_ref"] is None
    assert docs["check"]["check_id"] != tach["check"]["check_id"]
    assert docs["result_id"] != tach["result_id"]


def test_missing_check_identity_is_refused_not_inferred():
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_definition({"check_id": "docs"})
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result(
            {
                "kind": check_contract.RESULT_KIND,
                "schema_version": 1,
                "check": {"definition_version": 1},
                "source_ref": source(),
                "status": "passed",
                "result_id": "sha256:" + "0" * 64,
            }
        )
    assert check_contract.definition_for("not-a-check") is None


def test_native_failure_and_reason_are_preserved():
    failed = result(status="failed", instance="job-1")
    assert failed["status"] == "failed"
    assert failed["native_exit_code"] == 1
    check_contract.validate_result(failed)


def test_rule_or_origin_change_produces_a_different_result():
    definition = check_contract.definition_for("docs")
    rule_a = check_contract.rule_ref("hygiene", 1, digest="a" * 64)
    rule_b = check_contract.rule_ref("hygiene", 2, digest="b" * 64)
    base = {
        "source": source(),
        "status": "passed",
        "result_origin": "host",
        "result_instance": "job-1",
    }
    first = check_contract.native_result(definition, rule_ref=rule_a, **base)
    second = check_contract.native_result(definition, rule_ref=rule_b, **base)
    assert first["result_id"] != second["result_id"]
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result_set([first, second])


def test_duplicate_result_id_is_refused():
    one = result(instance="job-1")
    with pytest.raises(check_contract.CheckContractError):
        check_contract.validate_result_set([one, one])
