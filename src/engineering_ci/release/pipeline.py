"""Git/PR handoffs around native Dagger Checks; no saved execution receipts."""

from . import RELEASE_CONFIG
from .context import (
    CheckEvidence,
    CommitEvidence,
    MergeEvidence,
    PushEvidence,
    ReviewEvidence,
)


class EvidenceError(ValueError):
    pass


def current_checks(checkout):
    """Run the native check command and preserve its actual exit status."""
    view = getattr(getattr(checkout, "execution_context", None), "result", None)
    options = {"cwd": checkout.root, "check": False}
    if view and view.output_stream is not None:
        options.update(stdout=view.output_stream, stderr=view.output_stream)
    result = checkout.run(["dagger", "check"], **options)
    code = result.returncode if result.returncode >= 0 else 128 - result.returncode
    if view:
        view.local_checks = {
            "status": "passed" if code == 0 else "failed",
            "exit_code": code,
        }
        view.operations.append({"name": "dagger check", "exit_code": code})
        if code:
            view.stopped("local_checks", "Native Dagger Checks failed")
    return code


class DeliveryPipeline:
    def __init__(self, context, checkout):
        self.context, self.checkout = context, checkout
        if (
            context.root != checkout.root
            or context.facts["repository"] != checkout.repository
        ):
            raise EvidenceError("Delivery must use the same checkout identity")
        # This directory holds PR prose only; no check receipt is written or read.
        self.pr_body_path = checkout.root / ".test-tools/delivery/pr-body.md"

    def checked(self):
        code = current_checks(self.checkout)
        self.context.checks = CheckEvidence(code)
        return code

    def committed(self, facts):
        """Re-evaluate committed source through Dagger's own cache before push."""
        if not self.context.checks or self.context.checks.exit_code or facts["files"]:
            raise EvidenceError(
                "Commit requires successful native checks and a clean tree"
            )
        self.context.facts = facts
        code = current_checks(self.checkout)
        if code:
            raise EvidenceError(
                "Native checks failed after commit; stopped before push"
            )
        current = self.checkout.context(facts["version"])
        if current["files"] or current["head"] != facts["head"]:
            raise EvidenceError(
                "Checkout changed during native checks; stopped before push"
            )
        self.context.facts = current
        self.context.commit = CommitEvidence(facts["head"])

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
        if facts["branch"] == RELEASE_CONFIG["branch"]:
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
