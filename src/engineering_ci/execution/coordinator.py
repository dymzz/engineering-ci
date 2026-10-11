"""Workflow ordering and recovery independent of concrete transports."""
from __future__ import annotations

from typing import Callable, Protocol

from .fsm import ExecutionFSM
from .models import (CollectionIdentity, DispatchErrorInfo, DispatchErrorReason,
                     DispatchIdentity, DispatchRejected, DispatchUnknown,
                     ExecutionError, ExecutionResult, TaskRecord)
from .store import StateStore


class Collector(Protocol):
    def collect(self, record: TaskRecord, checkpoint: Callable[..., None]) -> None: ...
    def intact(self, record: TaskRecord) -> bool: ...


class ExecutionCoordinator:
    def __init__(self, store: StateStore, client, collector: Collector,
                 identity: DispatchIdentity, collection: CollectionIdentity,
                 inputs: dict[str, str], *, timeout=2700, interval=10):
        self.store, self.client, self.collector = store, client, collector
        self.identity, self.collection, self.inputs = identity, collection, inputs
        self.timeout, self.interval = timeout, interval
        self.fsm = ExecutionFSM()
        self._record: TaskRecord | None = None

    @property
    def record(self) -> TaskRecord:
        assert self._record is not None
        return self._record

    def event(self, event: str, **updates) -> None:
        """Persist each legal transition before allowing the next operation."""
        updated = self.fsm.advance(self.record, event, **updates)
        self.store.save(updated)
        self._record = updated

    def execute(self, *, resume: str | None = None, new_run=False, restart_finished=False,
                legacy_paths=()) -> ExecutionResult:
        with self.store.lock():
            self._record = self.store.load()
            if self._record is None and not new_run and resume in (None, 'last'):
                self._record = self.store.migrate(list(legacy_paths), self.identity, self.collection)
            if self._record and self.record.dispatch != self.identity:
                raise ExecutionError('Checkpoint dispatch identity does not match')
            if new_run:
                if self._record and self.record.phase not in ('FINISHED', 'REJECTED'):
                    print(f'WARNING: --new-run abandons {self.record.phase}; check GitHub for duplicates')
                self._record = None
            if (restart_finished and resume is None and self._record
                    and self.record.phase in ('FINISHED', 'REJECTED')):
                self._record = None  # An explicit validate/publish request may follow a known completed attempt.
            explicit_id = int(resume) if resume not in (None, 'last') else None
            if explicit_id is not None:
                self.client.get_run(self.identity, explicit_id)
                if self._record is None or self.record.run_id != explicit_id:
                    self._record = TaskRecord(self.identity, self.collection)
                    self.event('attach_run', run_id=explicit_id)
                elif self.record.collection != self.collection:
                    self._update_collection()
            elif resume == 'last' and self._record is None:
                raise ExecutionError('No saved Run ID; use --resume RUN_ID')
            if self._record:
                if self.record.phase == 'DISPATCHING':
                    self.event('uncertain', last_error=DispatchErrorInfo(
                        DispatchErrorReason.INTERRUPTED).to_state_error())
                if self.record.phase == 'UNKNOWN':
                    raise DispatchUnknown(details=DispatchErrorInfo.from_state_error(self.record.last_error))
                if self.record.phase == 'REJECTED' and resume is None:
                    self._record = None
                elif self.record.phase == 'REJECTED':
                    raise ExecutionError('Rejected request has no Run ID to resume')
                elif (self.record.phase == 'FINISHED' and resume is None
                      and self.record.collection == self.collection
                      and self.record.remote_conclusion == 'success' and self.collector.intact(self.record)):
                    self._record = None  # Preserve deliberate new invocation after prior full success.
                elif self.record.collection != self.collection:
                    self._update_collection()
            if self._record is None:
                # Bad local/remote targets leave existing state untouched.
                self.client.validate(self.identity)
                self._record = TaskRecord(self.identity, self.collection)
                self.event('validate')
            if self.record.phase == 'INIT':
                self.client.validate(self.identity)
                self.event('validate')
            if self.record.phase == 'VALIDATED':
                self.client.validate(self.identity)
                self.event('dispatch')
                try:
                    run_id = self.client.dispatch(self.identity, self.inputs)
                except DispatchRejected as exc:
                    self.event('reject', http_status=exc.status, last_error=exc.details.to_state_error())
                    raise
                except DispatchUnknown as exc:
                    self.event('uncertain', last_error=exc.details.to_state_error())
                    raise
                except (OSError, RuntimeError) as exc:
                    reason = (DispatchErrorReason.COMMAND_FAILED if isinstance(exc, OSError)
                              else DispatchErrorReason.UNEXPECTED_FAILURE)
                    details = DispatchErrorInfo(reason)
                    self.event('uncertain', last_error=details.to_state_error())
                    raise DispatchUnknown(details=details) from exc
                except KeyboardInterrupt:
                    self.event('uncertain', last_error=DispatchErrorInfo(
                        DispatchErrorReason.INTERRUPTED).to_state_error())
                    raise
                print(f'Run ID: {run_id}', flush=True)
                self.event('attach_run', run_id=run_id)
                print(f'Triggered: https://github.com/{self.identity.repo}/actions/runs/{run_id}', flush=True)
            if self.record.phase == 'PAUSED':
                self.event(f'resume_{self.record.paused_from.lower()}')
            if self.record.phase == 'FINISHED' and not self.collector.intact(self.record):
                self.event('recollect', collection_complete=False)
            try:
                if self.record.phase == 'RUNNING':
                    run = self.client.wait(self.identity, self.record.run_id, self.timeout, self.interval)
                    self.event('remote_completed', remote_status='completed',
                               remote_conclusion=run['conclusion'])
                if self.record.phase == 'COLLECTING':
                    if not self.collector.intact(self.record):
                        self.collector.collect(self.record, lambda **kw: self.event('checkpoint', **kw))
                    if not self.collector.intact(self.record):
                        raise ExecutionError('Required collection files are incomplete')
                    self.event('finish', collection_complete=True, last_error=None)
            except (ExecutionError, OSError, KeyboardInterrupt) as exc:
                if self.record.phase in ('RUNNING', 'COLLECTING'):
                    self.event('pause', last_error=type(exc).__name__)
                raise
            return ExecutionResult(self.record.run_id, self.record.remote_conclusion,
                                   self.record.collection_complete)

    def _update_collection(self) -> None:
        if self.record.phase == 'FINISHED':
            self.event('recollect', collection=self.collection, collection_complete=False)
        elif self.record.phase in ('INIT', 'VALIDATED'):
            self.event('configure', collection=self.collection, collection_complete=False)
        else:
            self.event('checkpoint', collection=self.collection, collection_complete=False)
