"""Collect official archives and verify each required local output."""
from __future__ import annotations

import copy
import os
import re
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from filelock import FileLock, Timeout

from .archive import ArchiveLimits, _safe_destination, extract_zip, safe_members
from .download import DownloadAdapter, ResourceChanged, file_digest
from .models import ExecutionError, RecoverableError, fingerprint
from .transport import ResourceIdentity


class ArtifactCollector:
    def __init__(self, client, adapter: DownloadAdapter, limits: ArchiveLimits | None = None):
        self.client, self.adapter = client, adapter
        self.limits = limits or ArchiveLimits()

    def _plan_key(self, record):
        return fingerprint([record.run_id, asdict(record.collection), record.remote_conclusion])

    def intact(self, record) -> bool:
        """Verify manifest hashes rather than trusting saved completion flags."""
        plan = record.files.get('plans', {}).get(self._plan_key(record))
        if not plan or not plan.get('artifacts_listed'):
            return False
        dest = Path(record.collection.out) / str(record.run_id)
        outputs = record.files.get('outputs', {})
        try:
            for key in plan['keys']:
                item = outputs.get(key, {})
                if item.get('status') == 'optional_missing' and not item.get('required', True):
                    continue
                if item.get('status') != 'complete' or not item.get('manifest'):
                    return False
                for relative, expected in item['manifest'].items():
                    path = _safe_destination(dest, relative)
                    if not path.is_file() or file_digest(path) != expected:
                        return False
            return True
        except (OSError, ExecutionError, KeyError, TypeError):
            return False

    def collect(self, record, checkpoint) -> None:
        """Keep log and artifact checkpoints separate so only missing work resumes."""
        dest = Path(record.collection.out) / str(record.run_id)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(dest / '.ghwf-collection.lock'), timeout=0):
                self._collect(record, checkpoint, dest)
        except Timeout as exc:
            raise RecoverableError('Another process is collecting this Run into the same directory') from exc

    def _collect(self, record, checkpoint, dest):
        cache = dest / '.ghwf-cache'
        if (cache.is_symlink() or dest.is_symlink()
                or not cache.resolve().is_relative_to(dest.resolve())):
            raise ExecutionError('Collection cache/output cannot be a symbolic link')
        cache.mkdir(exist_ok=True)
        files = copy.deepcopy(record.files)
        plans, outputs = files.setdefault('plans', {}), files.setdefault('outputs', {})
        key = self._plan_key(record)
        plan = plans.setdefault(key, {'keys': [], 'artifacts_listed': record.collection.no_artifacts})
        def save():
            checkpoint(files=copy.deepcopy(files))
        need_log = record.collection.save_log or record.remote_conclusion != 'success'
        if need_log:
            run = self.client.get_run(record.dispatch, record.run_id)
            attempt = run.get('run_attempt', 1)
            if plan.get('run_attempt', attempt) != attempt:
                raise ResourceChanged('Run attempt changed since the collection checkpoint')
            plan['run_attempt'] = attempt
            identity = ResourceIdentity(f'logs:{record.dispatch.repo}:{record.run_id}:attempt:{attempt}',
                                         immutable=True)
            log_key = fingerprint([str(dest), identity.key])
            if log_key not in plan['keys']:
                plan['keys'].append(log_key)
            item = outputs.setdefault(log_key, {'kind': 'log', 'required': True, 'status': 'pending',
                                                'resource': asdict(identity), 'manifest': {}})
            if not item['manifest']:
                item['manifest'] = self._legacy_owned(record, dest, log=True)
            save()
            if not self._item_intact(item, dest):
                endpoint = (f'repos/{record.dispatch.repo}/actions/runs/{record.run_id}'
                            f'/attempts/{attempt}/logs')
                def resolve_logs():
                    current = self.client.get_run(record.dispatch, record.run_id)
                    if current.get('run_attempt', 1) != attempt or current.get('status') != 'completed':
                        raise ResourceChanged('Run attempt changed while collecting logs')
                    return self.client.download_address(endpoint)
                zip_path = cache / f'logs-{attempt}.zip'
                self.adapter.download(resolve_logs, identity, zip_path)
                item['manifest'] = self._save_log(zip_path, dest, item.get('manifest', {}))
                item['status'] = 'complete'
                save()
        if not record.collection.no_artifacts:
            artifacts = self.client.artifacts(record.dispatch.repo, record.run_id)
            requested = record.collection.artifact
            if requested:
                requested = requested.replace('{run_id}', str(record.run_id))
                artifacts = [a for a in artifacts if a.name == requested]
            if not artifacts:
                if record.remote_conclusion == 'success':
                    save()
                    raise RecoverableError('Required artifact is missing; no collection completion recorded')
                missing_key = fingerprint([str(dest), record.run_id, requested, 'optional_missing'])
                outputs[missing_key] = {'kind': 'artifact', 'required': False, 'status': 'optional_missing',
                                        'manifest': {}}
                if missing_key not in plan['keys']:
                    plan['keys'].append(missing_key)
                print('No artifacts from failed Run; diagnostic logs are required', flush=True)
            else:
                if len({a.name.casefold() for a in artifacts}) != len(artifacts):
                    raise ExecutionError('Artifact names collide; select one using --artifact')
                for artifact in artifacts:
                    self._artifact_name(artifact.name)
                    identity = artifact.identity(record.dispatch.repo, record.run_id)
                    artifact_key = fingerprint([str(dest), identity.key, bool(requested)])
                    if artifact_key not in plan['keys']:
                        plan['keys'].append(artifact_key)
                    item = outputs.setdefault(artifact_key, {'kind': 'artifact', 'required': True,
                        'status': 'pending', 'resource': asdict(identity), 'manifest': {}})
                    if item.get('resource') != asdict(identity):
                        raise ResourceChanged('Artifact identity changed since the collection checkpoint')
                    if not item['manifest']:
                        item['manifest'] = self._legacy_owned(record, dest,
                            prefix='' if requested else artifact.name + '/')
                    save()
                    if self._item_intact(item, dest):
                        continue
                    if artifact.expired:
                        raise ExecutionError('Artifact has expired on GitHub')
                    def resolve_artifact(artifact=artifact, identity=identity):
                        current = self.client.artifact(record.dispatch.repo, artifact.id)
                        if current != artifact:
                            raise ResourceChanged('Artifact metadata changed while refreshing its download address')
                        return self.client.download_address(
                            f'repos/{record.dispatch.repo}/actions/artifacts/{artifact.id}/zip')
                    zip_path = cache / f'artifact-{artifact.id}.zip'
                    self.adapter.download(resolve_artifact, identity, zip_path)
                    item['manifest'] = extract_zip(zip_path, dest,
                        prefix='' if requested else artifact.name,
                        owned=item.get('manifest'), limits=self.limits)
                    item['status'] = 'complete'
                    save()
            plan['artifacts_listed'] = True
        save()

    def _item_intact(self, item, dest):
        try:
            return (item.get('status') == 'complete' and bool(item.get('manifest'))
                    and all(_safe_destination(dest, p).is_file()
                            and file_digest(_safe_destination(dest, p)) == sha
                            for p, sha in item['manifest'].items()))
        except (OSError, ExecutionError):
            return False

    @staticmethod
    def _artifact_name(name):
        if (not isinstance(name, str) or not name
                or name.casefold() in ('.', '..', '.ghwf-cache', '.ghwf-collection.lock')
                or any(c in name for c in '/\\:') or name.endswith((' ', '.'))
                or re.search(r'[\x00-\x1f]', name)
                or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I)):
            raise ExecutionError('Unsafe artifact name')

    @staticmethod
    def _legacy_owned(record, dest, *, log=False, prefix=''):
        """Adopt v1 declared outputs for replacement, still reverify remote ZIPs."""
        legacy = record.files.get('legacy', {})
        if legacy.get('collection', {}).get('out') != record.collection.out:
            return {}
        if log:
            names = ['run.log'] if legacy.get('log_saved') else []
        else:
            names = legacy.get('artifact_files', []) if legacy.get('artifacts_saved') else []
            if not isinstance(names, list):
                raise ExecutionError('Invalid legacy artifact manifest')
        owned = {}
        for name in names:
            if not isinstance(name, str) or not name.startswith(prefix):
                continue
            path = _safe_destination(dest, name)
            if path.is_file():
                owned[name] = file_digest(path)
        return owned

    def _save_log(self, zip_path, dest, owned):
        """Convert the official ZIP into the familiar atomic run.log output."""
        temp = None
        target = _safe_destination(dest, 'run.log')
        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = [m for m in safe_members(archive, self.limits) if not m.is_dir()]
                if not members:
                    raise ExecutionError('Log ZIP contains no logs')
                with tempfile.NamedTemporaryFile(mode='wb', dir=dest, prefix='.run-log-', delete=False) as stream:
                    temp = Path(stream.name)
                    for member in sorted(members, key=lambda m: m.filename):
                        stream.write(f'\n===== {member.filename} =====\n'.encode('utf-8'))
                        with archive.open(member) as source:
                            import shutil
                            shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            sha = file_digest(temp)
            if target.exists() and file_digest(target) != sha and owned.get('run.log') != file_digest(target):
                raise ExecutionError('Refusing to overwrite unexpected run.log')
            os.replace(temp, target)
            print(f'Saved log: {target}', flush=True)
            return {'run.log': sha}
        except ExecutionError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            raise ExecutionError('Invalid or corrupt log ZIP') from exc
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
