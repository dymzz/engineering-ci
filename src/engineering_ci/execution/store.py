"""Atomic JSON checkpoints and process-wide task exclusion."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from filelock import FileLock, Timeout

from .models import CollectionIdentity, DispatchIdentity, ExecutionError, TaskRecord


def atomic_json(path: Path, data: dict) -> None:
    """Replace the last checkpoint only after flushing a complete JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.ghwf-', suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


class StateStore:
    def __init__(self, path: Path, lock_timeout: float = 0):
        self.path = path
        self.lock_timeout = lock_timeout

    @contextmanager
    def lock(self):
        """Hold exclusion across loading, dispatch, polling and collection."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(self.path.with_suffix('.lock')), timeout=self.lock_timeout):
                yield
        except Timeout as exc:
            raise ExecutionError('This task is already managed by another ghwf process') from exc

    def load(self) -> TaskRecord | None:
        if not self.path.exists():
            return None
        try:
            return TaskRecord.from_dict(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ExecutionError(f'Invalid state file {self.path}; refusing blind dispatch') from exc

    def save(self, record: TaskRecord) -> None:
        record.validate()
        atomic_json(self.path, record.to_dict())

    def migrate(self, paths: list[Path], identity: DispatchIdentity,
                collection: CollectionIdentity) -> TaskRecord | None:
        """Import a matching v1 checkpoint, keeping the original for inspection."""
        candidates = []
        for path in paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('version') != 1:
                    continue
                if (str(data.get('repo', identity.repo)).lower(), data.get('workflow', identity.workflow),
                        data.get('ref', identity.ref)) != (
                        identity.repo, identity.workflow, identity.ref):
                    continue
                candidates.append((path, data))
            except (ValueError, OSError, AttributeError) as exc:
                raise ExecutionError(f'Unreadable legacy state {path}; refusing blind dispatch') from exc
        if not candidates:
            return None
        active = [(p, d) for p, d in candidates if d.get('stage') != 'finished']
        candidates = active or candidates
        if len({d.get('run_id') for _, d in candidates}) > 1:
            raise ExecutionError('Multiple legacy runs match; choose --resume RUN_ID')
        data = candidates[-1][1]
        stage = data.get('stage')
        run_id = data.get('run_id')
        phases = {'dispatch_pending': 'UNKNOWN', 'dispatch_rejected': 'REJECTED',
                  'running': 'RUNNING', 'dispatched': 'RUNNING', 'completed': 'COLLECTING',
                  'finished': 'COLLECTING'}
        if stage not in phases:
            raise ExecutionError(f'Unknown legacy stage {stage}; specify --resume RUN_ID')
        record = TaskRecord(identity, collection, phase=phases[stage], run_id=run_id,
                            remote_status='completed' if data.get('conclusion') else None,
                            remote_conclusion=data.get('conclusion'),
                            files={'legacy': {'collection': asdict(collection),
                                'log_saved': data.get('log_saved') is True,
                                'artifacts_saved': data.get('artifacts_saved') is True,
                                'artifact_files': data.get('artifact_files', [])}})
        if record.phase == 'COLLECTING' and not record.remote_conclusion:
            record = replace(record, phase='RUNNING')
        # Legacy booleans are hints only; collector revalidates local content.
        self.save(record)
        return record
