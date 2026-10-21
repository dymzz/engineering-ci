"""Evidence shared by local delivery operations and their admission guards."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .results import OperationResult


@dataclass(frozen=True)
class CheckEvidence:
    exit_code: int


@dataclass(frozen=True)
class CommitEvidence:
    head: str


@dataclass(frozen=True)
class PushEvidence:
    head: str


@dataclass(frozen=True)
class ReviewEvidence:
    number: int
    url: str
    head: str
    ci_status: str = "unavailable"


@dataclass(frozen=True)
class MergeEvidence:
    reviewed_head: str
    merge_commit: str
    main_commit: str


@dataclass
class ExecutionContext:
    root: Path
    facts: dict
    checks: CheckEvidence | None = None
    commit: CommitEvidence | None = None
    push: PushEvidence | None = None
    review: ReviewEvidence | None = None
    merge: MergeEvidence | None = None
    result: OperationResult | None = None
