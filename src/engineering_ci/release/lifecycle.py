"""Hierarchical local delivery FSM; remote Runs retain the execution FSM."""

import re
from types import SimpleNamespace

from transitions import MachineError
from transitions.extensions import HierarchicalMachine

from ..execution.fsm import InvalidTransition
from ..pipeline import PIPELINE
from .context import ExecutionContext, PushEvidence, ReviewEvidence

STATES = [
    "INSPECTING",
    {
        "name": "DELIVERY",
        "children": [
            "pending",
            "checking",
            "checked",
            "committing",
            "committed",
            "pushing",
            "pushed",
        ],
    },
    {"name": "REVIEW", "children": ["waiting", "merged"]},
    {"name": "PUBLICATION", "children": ["ready"]},
    {"name": "BLOCKED", "children": ["merged", "main", "identity"]},
]


class ReleaseLifecycle:
    def __init__(self, context: ExecutionContext):
        self.context = context
        self._model = SimpleNamespace()
        self._machine = HierarchicalMachine(
            model=self._model,
            states=STATES,
            initial="INSPECTING",
            auto_transitions=False,
        )
        transitions = [
            ("observe", "INSPECTING", "BLOCKED_identity", self._invalid_identity),
            (
                "observe",
                "INSPECTING",
                "BLOCKED_merged",
                lambda: self._merged() and bool(self.facts["files"]),
            ),
            ("observe", "INSPECTING", "REVIEW_merged", self._merged),
            (
                "observe",
                "INSPECTING",
                "BLOCKED_main",
                lambda: self.facts["branch"] == "main" and bool(self.facts["files"]),
            ),
            (
                "observe",
                "INSPECTING",
                "REVIEW_merged",
                lambda: self.facts["branch"] == "main",
            ),
            ("observe", "INSPECTING", "REVIEW_waiting", self._reviewed_push),
            ("observe", "INSPECTING", "DELIVERY_pending", lambda: True),
            ("start_checks", "DELIVERY_pending", "DELIVERY_checking", lambda: True),
            ("checks_passed", "DELIVERY_checking", "DELIVERY_checked", self._checked),
            ("start_commit", "DELIVERY_checked", "DELIVERY_committing", self._checked),
            ("committed", "DELIVERY_committing", "DELIVERY_committed", self._committed),
            ("start_push", "DELIVERY_committed", "DELIVERY_pushing", self._committed),
            ("pushed", "DELIVERY_pushing", "DELIVERY_pushed", self._pushed),
            (
                "open_review",
                "DELIVERY_pushed",
                "REVIEW_waiting",
                self._review_confirmed,
            ),
            (
                "confirm_merge",
                "REVIEW_merged",
                "PUBLICATION_ready",
                self._merge_confirmed,
            ),
            ("admit_publication", "PUBLICATION_ready", "=", self._merge_confirmed),
        ]
        for event, source, destination, guard in transitions:
            self._machine.add_transition(event, source, destination, conditions=guard)
        self.advance("observe")
        if self.phase == "REVIEW_waiting":
            self.context.push = PushEvidence(self.facts["head"])
            pr = self.facts["pr"]
            self.context.review = ReviewEvidence(
                pr["number"], pr["url"], pr["headRefOid"]
            )

    @property
    def facts(self):
        return self.context.facts

    @property
    def phase(self):
        return self._model.state

    def advance(self, event):
        """Only legal events with current predecessor evidence may advance."""
        try:
            if event not in self._machine.events or not self._model.trigger(event):
                raise InvalidTransition(
                    f"{event} lacks required evidence in {self.phase}"
                )
        except MachineError as error:
            raise InvalidTransition(
                f"Illegal release event {event} in {self.phase}"
            ) from error

    def _invalid_identity(self):
        return self.facts["local_version"] != self.facts["version"] or bool(
            self.facts["conflicts"]
        )

    def _merged(self):
        return bool(self.facts["pr"] and self.facts["pr"]["state"] == "MERGED")

    def _checked(self):
        evidence = self.context.checks
        return bool(
            evidence
            and evidence.working_digest == self.facts["working_digest"]
            and len(evidence.identities) == len(PIPELINE["profiles"]["pr"])
            and all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (evidence.sha256, *evidence.identities)
            )
        )

    def _committed(self):
        evidence = self.context.commit
        checks = self.context.checks
        return bool(
            evidence
            and checks
            and not self.facts["files"]
            and evidence.head == self.facts["head"]
            and evidence.checks_sha256 == checks.sha256
        )

    def _pushed(self):
        return bool(
            self._committed()
            and self.context.push
            and not self.facts["files"]
            and self.context.push.head
            == self.facts["head"]
            == self.facts["remote_head"]
        )

    def _review_confirmed(self):
        evidence = self.context.review
        return bool(
            self._pushed()
            and self._reviewed_push()
            and evidence
            and evidence.head == self.context.push.head
            and evidence.number == self.facts["pr"]["number"]
            and evidence.url == self.facts["pr"]["url"]
        )

    def _reviewed_push(self):
        pr = self.facts["pr"]
        return bool(
            pr
            and pr["state"] == "OPEN"
            and not self.facts["files"]
            and pr["headRefOid"] == self.facts["head"] == self.facts["remote_head"]
        )

    def _merge_confirmed(self):
        evidence = self.context.merge
        pr = self.facts["pr"]
        return bool(
            evidence
            and all(
                re.fullmatch(r"[0-9a-f]{40}", value)
                for value in (
                    evidence.reviewed_head,
                    evidence.merge_commit,
                    evidence.main_commit,
                )
            )
            and not self._invalid_identity()
            and not self.facts["files"]
            and evidence.reviewed_head == self.facts["head"]
            and (
                (
                    self.facts["branch"] == "main"
                    and evidence.main_commit == self.facts["head"]
                )
                or (
                    pr
                    and pr["state"] == "MERGED"
                    and pr["headRefOid"] == evidence.reviewed_head
                    and pr["mergeCommit"]["oid"] == evidence.merge_commit
                )
            )
        )

    def admit_publication(self, identity):
        """Pass the exact reviewed main SHA into the existing remote executor."""
        self.advance("admit_publication")
        if (
            identity["commit_sha"] != self.context.merge.main_commit
            or identity["version"] != self.facts["version"]
            or identity["ref"] != "main"
        ):
            raise InvalidTransition(
                "Publication identity differs from the merge evidence"
            )
