"""Typed execution identities, checkpoints and outcomes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
STATES = ('INIT', 'VALIDATED', 'DISPATCHING', 'UNKNOWN', 'REJECTED',
          'RUNNING', 'COLLECTING', 'PAUSED', 'FINISHED')


def fingerprint(value: object) -> str:
    """Hash a canonical identity without persisting input values."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DispatchIdentity:
    repo: str
    workflow: str
    ref: str
    inputs_sha256: str

    @classmethod
    def create(cls, repo: str, workflow: str, ref: str, inputs: dict[str, str]):
        return cls(repo.lower(), workflow, ref, fingerprint(inputs))

    @property
    def key(self) -> str:
        return fingerprint(asdict(self))[:24]


@dataclass(frozen=True)
class CollectionIdentity:
    out: str
    artifact: str | None = None
    save_log: bool = False
    no_artifacts: bool = False

    @classmethod
    def create(cls, out: Path, artifact=None, save_log=False, no_artifacts=False):
        return cls(str(out.resolve()), artifact, save_log, no_artifacts)


@dataclass(frozen=True)
class TaskRecord:
    dispatch: DispatchIdentity
    collection: CollectionIdentity
    phase: str = 'INIT'
    schema_version: int = SCHEMA_VERSION
    run_id: int | None = None
    paused_from: str | None = None
    remote_status: str | None = None
    remote_conclusion: str | None = None
    collection_complete: bool = False
    files: dict = field(default_factory=dict)
    last_error: str | None = None
    http_status: int | None = None
    updated_at: str = field(default_factory=timestamp)
    dispatch_started_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        if data.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('Unsupported execution state schema')
        value = dict(data)
        value['dispatch'] = DispatchIdentity(**value['dispatch'])
        value['collection'] = CollectionIdentity(**value['collection'])
        record = cls(**value)
        record.validate()
        return record

    def validate(self) -> None:
        """Reject corrupted checkpoints before any remote side effect."""
        if self.phase not in STATES:
            raise ValueError(f'Unknown phase: {self.phase}')
        if (not isinstance(self.dispatch.repo, str)
                or not re.fullmatch(r'[\w.-]+/[\w.-]+', self.dispatch.repo)
                or not isinstance(self.dispatch.workflow, str) or not self.dispatch.workflow
                or not isinstance(self.dispatch.ref, str) or not self.dispatch.ref
                or not re.fullmatch(r'[0-9a-f]{64}', self.dispatch.inputs_sha256)):
            raise ValueError('Invalid dispatch identity')
        if (not isinstance(self.collection.out, str) or not Path(self.collection.out).is_absolute()
                or type(self.collection.save_log) is not bool or type(self.collection.no_artifacts) is not bool
                or self.collection.artifact is not None and not isinstance(self.collection.artifact, str)
                or type(self.collection_complete) is not bool or not isinstance(self.files, dict)):
            raise ValueError('Invalid collection checkpoint')
        if self.run_id is not None and (type(self.run_id) is not int or self.run_id <= 0):
            raise ValueError('Invalid Run ID')
        if self.phase in ('RUNNING', 'COLLECTING', 'FINISHED') and self.run_id is None:
            raise ValueError(f'{self.phase} requires a Run ID')
        if self.phase == 'PAUSED' and self.paused_from not in ('RUNNING', 'COLLECTING'):
            raise ValueError('PAUSED requires its original phase')
        if self.phase == 'PAUSED' and self.run_id is None:
            raise ValueError('PAUSED requires a Run ID')
        if self.phase in ('COLLECTING', 'FINISHED') and (
                self.remote_status != 'completed' or not self.remote_conclusion):
            raise ValueError('Collection requires a determined remote result')
        if self.phase == 'FINISHED' and not self.collection_complete:
            raise ValueError('FINISHED requires completed collection')


class ExecutionError(RuntimeError):
    """Safe, typed failure; messages must never contain credentials or URLs."""


class RecoverableError(ExecutionError):
    pass


class DispatchRejected(ExecutionError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f'Dispatch rejected by GitHub (HTTP {status}); fix the request')


class DispatchUnknown(ExecutionError):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    run_id: int
    remote_conclusion: str
    collection_complete: bool

    @property
    def exit_code(self) -> int:
        return 0 if self.remote_conclusion == 'success' and self.collection_complete else 1
