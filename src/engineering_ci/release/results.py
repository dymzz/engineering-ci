"""Read-only operation views; execution evidence and guards remain authoritative."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ExecutionContext


@dataclass(frozen=True)
class NextAction:
    kind: str
    argv: tuple[str, ...] = ()
    requires_human: bool = False
    prerequisite: str | None = None


@dataclass
class OperationResult:
    operation: str
    repository: str
    version: str
    context: ExecutionContext | None = field(default=None, repr=False)
    lifecycle: object | None = field(default=None, repr=False)
    output_stream: object | None = field(default=None, repr=False)
    exit_code: int | None = None
    status: str = "running"
    stop: dict | None = None
    local_checks: dict = field(default_factory=lambda: {"status": "not_inspected"})
    review: dict | None = None
    executions: list[dict] = field(default_factory=list)
    publication: dict = field(default_factory=lambda: {"status": "unconfirmed"})
    operations: list[dict] = field(default_factory=list)
    next_actions: list[NextAction] = field(default_factory=list)

    def action(self, kind, argv=(), *, requires_human=False, prerequisite=None):
        """Record executable arguments without interpreting them as shell code."""
        item = NextAction(kind, tuple(argv), requires_human, prerequisite)
        if item not in self.next_actions:
            self.next_actions.append(item)

    def stopped(self, code, reason):
        self.stop = {"code": code, "reason": reason}

    def finish(self, exit_code):
        """Preserve the original operation exit code, independently of publication."""
        self.exit_code = exit_code
        waiting = self.stop and self.stop["code"] in {
            "environment",
            "delivery_blocked",
            "undelivered_after_merge",
            "dispatch_unknown",
            "existing_run",
            "human_review",
            "pending_delivery",
        }
        waiting = waiting or (
            exit_code == 3
            and self.stop
            and (
                self.stop["code"] == "local_checks"
                or self.stop["code"] == "pr_ci"
                and (self.review or {}).get("ci_status") != "failed"
            )
        )
        self.status = (
            "completed"
            if exit_code == 0
            else "interrupted"
            if exit_code == 130
            else "stopped"
            if waiting
            else "failed"
        )

    def checks_view(self, report, path, *, current=None, inspection=False):
        """Project existing check reports without copying logs or environment values."""
        fields = (
            "name",
            "status",
            "exit_code",
            "freshness",
            "executed",
            "executed_this_invocation",
            "reused",
            "passed_for_current_input",
            "counts",
        )
        self.local_checks = {
            "status": "passed"
            if current is True
            else "not_current_pass"
            if current is False
            else "recorded",
            "report": str(path),
            "checks": [
                {
                    **{key: entry[key] for key in fields if key in entry},
                    "platform": (entry.get("environment") or {}).get("platform"),
                    "identity_sha256": (entry.get("identity") or {}).get("sha256"),
                    **(
                        {"executed_this_invocation": False, "reused": False}
                        if inspection
                        else {}
                    ),
                }
                for entry in report.get("checks", [])
            ],
        }

    def published(self, receipt):
        """Expose only verified Release identity and the original evidence attempt."""
        self.publication = {
            "status": receipt["status"],
            "release_url": receipt["release_url"],
            "commit_sha": receipt.get("commit_sha"),
            "workflow": {
                key: value
                for key, value in receipt.get("manifest", {})
                .get("workflow", {})
                .items()
                if key in {"run_id", "run_url", "run_attempt"}
            },
        }

    def to_dict(self):
        """Build a snapshot from the same live context used by the FSM and Pipeline."""
        delivery = {"phase": getattr(self.lifecycle, "phase", None)}
        if self.context:
            facts = self.context.facts
            delivery.update(
                {
                    key: facts.get(key)
                    for key in (
                        "branch",
                        "head",
                        "remote_head",
                        "local_version",
                        "files",
                        "permission",
                    )
                }
            )
            delivery["checkout"] = str(self.context.root)
            pr = facts.get("pr")
            delivery["pr"] = (
                {
                    key: pr.get(key)
                    for key in (
                        "number",
                        "url",
                        "state",
                        "isDraft",
                        "headRefOid",
                    )
                }
                if pr
                else None
            )
            delivery["evidence"] = {}
            for name in ("checks", "commit", "push", "review", "merge"):
                receipt = getattr(self.context, name)
                if receipt:
                    value = asdict(receipt)
                    if name == "checks":
                        value["report"] = str(value["report"])
                    delivery["evidence"][name] = value
        return {
            "schema_version": 1,
            "operation": self.operation,
            "repository": self.repository,
            "version": self.version,
            "tag": "v" + self.version,
            "status": self.status,
            "exit_code": self.exit_code,
            "delivery": delivery,
            "local_checks": self.local_checks,
            "review": self.review,
            "executions": self.executions,
            "publication": self.publication,
            "operations": self.operations,
            "stop": self.stop,
            "next_actions": [asdict(action) for action in self.next_actions],
        }

    def render(self):
        """Render the same final result used by JSON, after existing progress output."""
        value = self.to_dict()
        print(
            f"Operation: {value['operation']} / {value['status']}; exit: {value['exit_code']}"
        )
        print(f"Publication: {value['publication']['status']}")
        if value["stop"]:
            print(f"Stop [{value['stop']['code']}]: {value['stop']['reason']}")
        for action in self.next_actions:
            if action.argv:
                print(f"Next [{action.kind}]: {command_text(action.argv)}")
            else:
                print(f"Next [{action.kind}]: human decision required")
            if action.prerequisite:
                print(f"  Requires: {action.prerequisite}")


def command_text(argv):
    """Render argument lists for the current shell, without executing them."""

    def quote(value):
        if os.name == "nt":
            return (
                value
                if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value)
                else "'" + value.replace("'", "''") + "'"
            )
        return shlex.quote(value)

    return " ".join(quote(value) for value in argv)
