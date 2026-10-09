"""Typed execution identities, checkpoints and outcomes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
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


class DispatchErrorReason(str, Enum):
    HTTP_REJECTED = 'http_rejected'
    INVALID_REF = 'invalid_ref'
    WORKFLOW_NOT_DISPATCHABLE = 'workflow_not_dispatchable'
    ACCEPTED_WITHOUT_RUN_ID = 'accepted_without_run_id'
    HTTP_ERROR = 'http_error'
    NETWORK_TIMEOUT = 'network_timeout'
    NETWORK_INTERRUPTED = 'network_interrupted'
    COMMAND_FAILED = 'command_failed'
    EMPTY_RESPONSE = 'empty_response'
    INVALID_JSON = 'invalid_json'
    UNEXPECTED_RESPONSE_TYPE = 'unexpected_response_type'
    MISSING_RUN_ID = 'missing_run_id'
    INVALID_RUN_ID = 'invalid_run_id'
    MALFORMED_HTTP_RESPONSE = 'malformed_http_response'
    MISSING_HTTP_STATUS = 'missing_http_status'
    INTERRUPTED = 'interrupted'
    UNEXPECTED_FAILURE = 'unexpected_failure'
    RESPONSE_UNAVAILABLE = 'response_unavailable'


_DISPATCH_MESSAGES = {
    DispatchErrorReason.HTTP_REJECTED: 'GitHub explicitly rejected the Dispatch; fix the request',
    DispatchErrorReason.INVALID_REF: 'GitHub rejected the requested ref',
    DispatchErrorReason.WORKFLOW_NOT_DISPATCHABLE: 'Workflow does not support workflow_dispatch',
    DispatchErrorReason.ACCEPTED_WITHOUT_RUN_ID: 'GitHub accepted the Dispatch but returned no Run ID',
    DispatchErrorReason.HTTP_ERROR: 'HTTP response does not establish a Dispatch result',
    DispatchErrorReason.NETWORK_TIMEOUT: 'Dispatch transport timed out before a reliable result',
    DispatchErrorReason.NETWORK_INTERRUPTED: 'Dispatch transport was interrupted before a reliable result',
    DispatchErrorReason.COMMAND_FAILED: 'GitHub CLI did not complete with a reliable Dispatch result',
    DispatchErrorReason.EMPTY_RESPONSE: 'Dispatch response body is empty',
    DispatchErrorReason.INVALID_JSON: 'Dispatch response body is not valid JSON',
    DispatchErrorReason.UNEXPECTED_RESPONSE_TYPE: 'Dispatch response must be a JSON object',
    DispatchErrorReason.MISSING_RUN_ID: 'Dispatch response has no workflow_run_id',
    DispatchErrorReason.INVALID_RUN_ID: 'workflow_run_id must be a positive integer',
    DispatchErrorReason.MALFORMED_HTTP_RESPONSE: 'Dispatch HTTP response framing is invalid',
    DispatchErrorReason.MISSING_HTTP_STATUS: 'GitHub CLI output has no reliable HTTP status',
    DispatchErrorReason.INTERRUPTED: 'Dispatch interrupted before a durable result',
    DispatchErrorReason.UNEXPECTED_FAILURE: 'Dispatch failed before a reliable result was recorded',
    DispatchErrorReason.RESPONSE_UNAVAILABLE: 'Dispatch response is unavailable',
}


@dataclass(frozen=True)
class DispatchErrorInfo:
    """Allowlisted diagnostics; raw headers, bodies and exception text are excluded."""
    reason: DispatchErrorReason
    http_status: int | None = None
    process_exit_code: int | None = None

    def __post_init__(self):
        if not isinstance(self.reason, DispatchErrorReason):
            raise ValueError('Invalid Dispatch error reason')
        if self.http_status is not None and (type(self.http_status) is not int
                                            or not 100 <= self.http_status <= 599):
            raise ValueError('Invalid Dispatch HTTP status')
        if self.process_exit_code is not None and type(self.process_exit_code) is not int:
            raise ValueError('Invalid Dispatch process exit code')

    @property
    def safe_message(self) -> str:
        return _DISPATCH_MESSAGES[self.reason]

    def to_state_error(self) -> str:
        """Retain structured diagnostics in the existing last_error string field."""
        return json.dumps({'reason': self.reason.value, 'http_status': self.http_status,
                           'process_exit_code': self.process_exit_code,
                           'safe_message': self.safe_message}, sort_keys=True)

    @classmethod
    def from_state_error(cls, value: str | None):
        try:
            data = json.loads(value)
            return cls(DispatchErrorReason(data['reason']), data.get('http_status'),
                       data.get('process_exit_code'))
        except (ValueError, TypeError, KeyError, AttributeError):
            return None  # Older checkpoints used plain text.


class DispatchRejected(ExecutionError):
    def __init__(self, status: int, *, details: DispatchErrorInfo | None = None):
        self.details = details or DispatchErrorInfo(DispatchErrorReason.HTTP_REJECTED, status)
        self.status = self.http_status = status
        if self.details.http_status != status:
            raise ValueError('Dispatch rejection status does not match its diagnostic')
        self.reason = self.details.reason
        super().__init__(f'Dispatch rejected by GitHub (HTTP {status}): {self.details.safe_message}')


class DispatchUnknown(ExecutionError):
    def __init__(self, message: str | None = None, *, details: DispatchErrorInfo | None = None):
        # Keep legacy constructor calls valid without echoing untrusted error text.
        self.details = details or DispatchErrorInfo(DispatchErrorReason.RESPONSE_UNAVAILABLE)
        self.http_status = self.details.http_status
        self.reason = self.details.reason
        status = f' (HTTP {self.http_status})' if self.http_status is not None else ''
        super().__init__(f'Dispatch outcome UNKNOWN [{self.reason.value}]{status}: '
                         f'{self.details.safe_message}; inspect GitHub and use --resume RUN_ID')


@dataclass(frozen=True)
class ExecutionResult:
    run_id: int
    remote_conclusion: str
    collection_complete: bool

    @property
    def exit_code(self) -> int:
        return 0 if self.remote_conclusion == 'success' and self.collection_complete else 1
