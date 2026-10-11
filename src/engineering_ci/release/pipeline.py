"""Produce stage receipts for the context used by the release lifecycle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import checks
from ..pipeline import PIPELINE
from .context import (
    CheckEvidence,
    CommitEvidence,
    MergeEvidence,
    PushEvidence,
    ReviewEvidence,
)


class EvidenceError(ValueError):
    pass


def current_checks(checkout, path):
    """Reuse each matching execution; refresh only missing or invalidated checks."""
    print(f"Local PR checks: checking per-check evidence in {path}")
    output_options = {}
    try:
        saved = checks.read_report(path)
        parameters = [entry["identity"]["parameters"] for entry in saved["checks"]]
        # These are output options, not selectors or a weaker test policy.
        if parameters and all(value == parameters[0] for value in parameters):
            value = parameters[0]
            if (
                set(value) == {"coverage", "junit"}
                and type(value["coverage"]) is bool
                and isinstance(value["junit"], str)
            ):
                if value["coverage"]:
                    output_options["coverage"] = True
                if value["junit"] != "core-junit.xml":
                    output_options["junit"] = value["junit"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    _, code = checks.run_checks(
        [PIPELINE["delivery"]["checks_profile"]],
        root=checkout.root,
        output=path,
        reuse=True,
        **output_options,
    )
    return code


def check_receipt(checkout, path):
    """Do not push if commits or hooks changed the inputs after validation."""
    result, code = checks.inspect(path, root=checkout.root)
    if code or result["overall_status"] != "passed":
        raise EvidenceError(
            "Inputs changed after checking; delivery stopped before push"
        )
    return result


class DeliveryPipeline:
    def __init__(self, context, checkout, report=None):
        self.context, self.checkout = context, checkout
        if (
            context.root != checkout.root
            or context.facts["repository"] != checkout.repository
        ):
            raise EvidenceError("Pipeline and FSM must use the same checkout identity")
        path = Path(report) if report else checkout.root / ".test-tools/checks/pr.json"
        self.report = checks.report_path(
            path if path.is_absolute() else checkout.root / path, checkout.root
        )

    def checked(self):
        """Save only complete, currently valid check identities, without rerunning a pass."""
        code = current_checks(self.checkout, self.report)
        if code:
            return code
        raw = self.report.read_bytes()
        current = check_receipt(self.checkout, self.report)
        if self.report.read_bytes() != raw:
            raise EvidenceError(
                "Check receipt changed while reading admission evidence"
            )
        saved = json.loads(raw)
        names = PIPELINE["profiles"][PIPELINE["delivery"]["checks_profile"]]
        if (
            [entry["name"] for entry in saved["checks"]] != names
            or [entry["name"] for entry in current["checks"]] != names
            or not all(entry["passed_for_current_input"] for entry in current["checks"])
        ):
            raise EvidenceError("A partial profile cannot admit delivery")
        identities = tuple(entry["identity"]["sha256"] for entry in saved["checks"])
        if not all(isinstance(value, str) and len(value) == 64 for value in identities):
            raise EvidenceError("Check identities are missing")
        self.context.checks = CheckEvidence(
            self.report,
            hashlib.sha256(raw).hexdigest(),
            identities,
            self.context.facts["working_digest"],
        )
        return 0

    def committed(self, facts):
        """Bind the current check receipt to the exact clean committed inputs."""
        evidence = self.context.checks
        if not evidence or facts["files"]:
            raise EvidenceError(
                "Commit requires a complete check receipt and clean inputs"
            )
        check_receipt(self.checkout, evidence.report)
        if hashlib.sha256(evidence.report.read_bytes()).hexdigest() != evidence.sha256:
            raise EvidenceError("Check receipt changed after admission")
        self.context.facts = facts
        self.context.commit = CommitEvidence(facts["head"], evidence.sha256)

    def pushed(self, facts):
        """Confirm the actual remote ref, rather than assuming push success."""
        commit = self.context.commit
        if (
            not commit
            or facts["files"]
            or facts["head"] != commit.head
            or facts["remote_head"] != commit.head
        ):
            raise EvidenceError("Push does not correspond to the admitted commit")
        self.context.facts = facts
        self.context.push = PushEvidence(commit.head)

    def reviewed(self, facts):
        """Require an OPEN PR for the just-confirmed remote commit."""
        push, pr = self.context.push, facts["pr"]
        if (
            not push
            or not pr
            or pr["state"] != "OPEN"
            or facts["files"]
            or not (
                push.head == facts["head"] == facts["remote_head"] == pr["headRefOid"]
            )
        ):
            raise EvidenceError("PR does not correspond to the confirmed push")
        self.context.facts = facts
        self.context.review = ReviewEvidence(pr["number"], pr["url"], push.head)

    def merged(self):
        """Use GitHub merge ancestry and main identity as publication inputs."""
        facts = self.context.facts
        if facts["files"]:
            raise EvidenceError("Undelivered local changes cannot enter publication")
        pr = facts["pr"]
        if facts["branch"] == PIPELINE["delivery"]["release"]["branch"]:
            main = self.checkout.main_commit()
            if main != facts["head"]:
                raise EvidenceError(
                    "Local main and remote main differ; update main before publication"
                )
            merge = main
        elif pr and pr["state"] == "MERGED":
            main = self.checkout.merged_commit(pr)
            merge = pr["mergeCommit"]["oid"]
        else:
            raise EvidenceError("Human merge evidence is missing")
        self.context.merge = MergeEvidence(facts["head"], merge, main)
        return main
