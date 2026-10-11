"""Stage evidence gates a hierarchical lifecycle, never a flat test/job matrix."""

from pathlib import Path

import pytest

from engineering_ci import checks
from engineering_ci.execution.fsm import InvalidTransition
from engineering_ci.release.context import (
    CheckEvidence,
    CommitEvidence,
    ExecutionContext,
    MergeEvidence,
    PushEvidence,
    ReviewEvidence,
)
from engineering_ci.release.lifecycle import ReleaseLifecycle
from engineering_ci.release.pipeline import DeliveryPipeline, EvidenceError

HEAD = "a" * 40
MERGE = "b" * 40


def context(tmp_path, *, branch="codex/fix", files=None, pr=None, remote=None):
    return ExecutionContext(
        tmp_path,
        {
            "repository": "dymzz/engineering-ci",
            "version": "0.4.11",
            "local_version": "0.4.11",
            "branch": branch,
            "head": HEAD,
            "remote_head": remote,
            "files": files or [],
            "working_digest": "input",
            "pr": pr,
            "conflicts": "",
        },
    )


def pr(state="OPEN"):
    return {
        "number": 12,
        "url": "https://github.com/dymzz/engineering-ci/pull/12",
        "state": state,
        "headRefOid": HEAD,
        "mergeCommit": {"oid": MERGE},
    }


def check_evidence(tmp_path):
    return CheckEvidence(
        tmp_path / "checks.json", "f" * 64, ("e" * 64,) * len(checks.PR_CHECKS), "input"
    )


def test_hierarchical_delivery_requires_each_predecessor(tmp_path):
    execution = context(tmp_path, files=["source.py"])
    fsm = ReleaseLifecycle(execution)
    assert fsm.phase == "DELIVERY_pending"
    with pytest.raises(InvalidTransition):
        fsm.advance("start_push")
    fsm.advance("start_checks")
    with pytest.raises(InvalidTransition, match="evidence"):
        fsm.advance("checks_passed")
    execution.checks = check_evidence(tmp_path)
    fsm.advance("checks_passed")
    fsm.advance("start_commit")
    with pytest.raises(InvalidTransition):
        fsm.advance("committed")
    execution.facts["files"] = []
    execution.commit = CommitEvidence(HEAD, execution.checks.sha256)
    fsm.advance("committed")
    fsm.advance("start_push")
    execution.push = PushEvidence(HEAD)
    with pytest.raises(InvalidTransition):
        fsm.advance("pushed")
    execution.facts["remote_head"] = HEAD
    fsm.advance("pushed")
    execution.facts["pr"] = pr()
    with pytest.raises(InvalidTransition):
        fsm.advance("open_review")
    execution.review = ReviewEvidence(12, pr()["url"], HEAD)
    fsm.advance("open_review")
    assert fsm.phase == "REVIEW_waiting"
    with pytest.raises(InvalidTransition):
        fsm.advance("confirm_merge")
    assert not hasattr(fsm._model, "to_PUBLICATION_ready")


@pytest.mark.parametrize("stage", ["checks", "commit", "push", "review"])
def test_old_or_missing_stage_evidence_cannot_advance(tmp_path, stage):
    execution = context(tmp_path)
    fsm = ReleaseLifecycle(execution)
    fsm.advance("start_checks")
    execution.checks = check_evidence(tmp_path)
    if stage == "checks":
        execution.facts["working_digest"] = "changed"
        event = "checks_passed"
    else:
        fsm.advance("checks_passed")
        fsm.advance("start_commit")
        execution.commit = CommitEvidence(HEAD, execution.checks.sha256)
        if stage == "commit":
            execution.commit = CommitEvidence(HEAD, "0" * 64)
            event = "committed"
        else:
            fsm.advance("committed")
            fsm.advance("start_push")
            execution.push = PushEvidence(HEAD)
            execution.facts["remote_head"] = HEAD
            if stage == "push":
                execution.facts["head"] = MERGE
                event = "pushed"
            else:
                fsm.advance("pushed")
                execution.facts["pr"] = pr()
                execution.review = ReviewEvidence(12, pr()["url"], MERGE)
                event = "open_review"
    with pytest.raises(InvalidTransition):
        fsm.advance(event)


def test_merged_pr_with_undelivered_inputs_never_becomes_ready(tmp_path):
    execution = context(tmp_path, files=["source.py"], pr=pr("MERGED"))
    fsm = ReleaseLifecycle(execution)
    assert fsm.phase == "BLOCKED_merged"
    execution.merge = MergeEvidence(HEAD, MERGE, MERGE)
    with pytest.raises(InvalidTransition):
        fsm.advance("confirm_merge")


def test_restart_observes_reviewed_push_without_fabricating_local_checks(tmp_path):
    execution = context(tmp_path, pr=pr(), remote=HEAD)
    fsm = ReleaseLifecycle(execution)
    assert fsm.phase == "REVIEW_waiting"
    assert execution.push.head == execution.review.head == HEAD
    assert execution.checks is None


def test_pipeline_and_fsm_hold_one_context_and_exact_merge_evidence(tmp_path):
    execution = context(tmp_path, pr=pr("MERGED"))

    class Checkout:
        root = tmp_path
        repository = "dymzz/engineering-ci"

        def merged_commit(self, value):
            assert value is execution.facts["pr"]
            return MERGE

    pipeline = DeliveryPipeline(execution, Checkout())
    fsm = ReleaseLifecycle(execution)
    assert pipeline.context is fsm.context is execution
    with pytest.raises(InvalidTransition):
        fsm.advance("confirm_merge")
    assert pipeline.merged() == MERGE
    fsm.advance("confirm_merge")
    assert fsm.phase == "PUBLICATION_ready"
    identity = {"version": "0.4.11", "commit_sha": MERGE, "ref": "main"}
    fsm.admit_publication(identity)
    with pytest.raises(InvalidTransition, match="identity"):
        fsm.admit_publication({**identity, "commit_sha": HEAD})
    execution.facts["files"] = ["later.py"]
    with pytest.raises(InvalidTransition):
        fsm.admit_publication(identity)


def test_mismatched_pipeline_checkout_is_rejected(tmp_path):
    execution = context(tmp_path)

    class Checkout:
        root = Path("elsewhere")
        repository = "dymzz/engineering-ci"

    with pytest.raises(EvidenceError, match="identity"):
        DeliveryPipeline(execution, Checkout())
