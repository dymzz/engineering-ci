"""P0-3: coordinator/client/collector integration using injectable external I/O."""
import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from urllib.error import HTTPError
from email.message import Message

import pytest

from engineering_ci.execution.collector import ArtifactCollector
from engineering_ci.execution.coordinator import ExecutionCoordinator
from engineering_ci.execution.download import (DownloadAdapter, DownloadConfig, HTTPMetadata,
                                               ResourceChanged)
from engineering_ci.execution.github import (ArtifactInfo, CommandResult, GitHubActionsClient)
from engineering_ci.execution.models import (CollectionIdentity, DispatchIdentity, ExecutionError,
                                             RecoverableError, TaskRecord)
from engineering_ci.execution.store import StateStore, atomic_json
from engineering_ci.execution.transport import DownloadProgress, TransportResult


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class ActionsFake:
    def __init__(self):
        self.log = zip_bytes({'job/1_setup.txt': 'setup OK', 'job/2_test.txt': 'test error details'})
        self.body = zip_bytes({'result.txt': 'build output'})
        self.info = ArtifactInfo(7, 'build-123', len(self.body),
                                 'sha256:' + hashlib.sha256(self.body).hexdigest())
        self.posts = 0
        self.conclusion = 'success'
        self.attempt = 1
        self.no_artifact = False
        self.urls = []
    def validate(self, identity):
        pass
    def dispatch(self, identity, inputs):
        self.posts += 1
        return 123
    def get_run(self, identity, run_id):
        return {'id': run_id, 'status': 'completed', 'conclusion': self.conclusion,
                'run_attempt': self.attempt}
    def wait(self, identity, run_id, timeout, interval):
        return self.get_run(identity, run_id)
    def artifacts(self, repo, run_id):
        return [] if self.no_artifact else [self.info]
    def artifact(self, repo, artifact_id):
        return self.info
    def download_address(self, endpoint):
        self.urls.append(endpoint)
        return 'https://download.invalid/' + ('log' if endpoint.endswith('logs') else 'artifact') + '?secret=SIGNED'


class ArchiveFake:
    def __init__(self, client):
        self.client = client
        self.calls = []
        self.fail_kind = None
    def download(self, resolve_url, identity, target):
        kind = 'log' if identity.key.startswith('logs:') else 'artifact'
        self.calls.append(kind)
        resolve_url()
        if self.fail_kind == kind:
            target.write_bytes(b'partial')
            raise RecoverableError('Transport interrupted')
        target.write_bytes(self.client.log if kind == 'log' else self.client.body)
        return TransportResult(target, DownloadProgress(target.stat().st_size, None, False))


@pytest.fixture
def workflow(tmp_path):
    client = ActionsFake()
    adapter = ArchiveFake(client)
    collector = ArtifactCollector(client, adapter)
    store = StateStore(tmp_path / 'state.json')
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    collection = CollectionIdentity.create(tmp_path / 'out', 'build-{run_id}', True)
    def create(collection=collection):
        return ExecutionCoordinator(store, client, collector, identity, collection, {})
    return client, adapter, store, collection, create


def test_normal_full_collection(workflow):
    client, adapter, store, collection, create = workflow
    assert create().execute().exit_code == 0
    out = Path(collection.out) / '123'
    assert (out / 'result.txt').read_text() == 'build output'
    assert 'test error details' in (out / 'run.log').read_text()
    assert store.load().remote_status == 'completed'
    assert store.load().remote_conclusion == 'success'
    assert adapter.calls == ['log', 'artifact']
    assert 'SIGNED' not in store.path.read_text()
    create().execute(resume='last')
    assert adapter.calls == ['log', 'artifact'] and client.posts == 1


@pytest.mark.parametrize('kind', ['log', 'artifact'])
def test_partial_collection_resumes_only_missing_files(workflow, kind):
    client, adapter, store, _, create = workflow
    adapter.fail_kind = kind
    with pytest.raises(RecoverableError):
        create().execute()
    assert store.load().phase == 'PAUSED' and store.load().paused_from == 'COLLECTING'
    first = list(adapter.calls)
    adapter.fail_kind = None
    client.wait = lambda *a: (_ for _ in ()).throw(AssertionError('Collection must not wait again'))
    create().execute()
    assert client.posts == 1
    if kind == 'artifact':
        assert adapter.calls == first + ['artifact']
    else:
        assert adapter.calls == first + ['log', 'artifact']


def test_remote_failure_always_saves_diagnostics(workflow):
    client, adapter, store, collection, create = workflow
    client.conclusion = 'failure'
    client.no_artifact = True
    collection = replace(collection, save_log=False)
    assert create(collection).execute().exit_code == 1
    assert 'test error details' in (Path(collection.out) / '123/run.log').read_text()
    assert store.load().phase == 'FINISHED' and adapter.calls == ['log']


def test_bad_zip_never_finishes(workflow):
    _, adapter, store, _, create = workflow
    adapter.client.body = b'corrupt ZIP'
    with pytest.raises(ExecutionError, match='corrupt ZIP'):
        create().execute()
    assert store.load().phase == 'PAUSED' and not store.load().collection_complete


def test_file_corruption_never_reported_complete(workflow):
    _, adapter, store, collection, create = workflow
    create().execute()
    (Path(collection.out) / '123/result.txt').write_text('corrupt output')
    with pytest.raises(ExecutionError, match='overwrite'):
        create().execute(resume='last')
    assert store.load().phase == 'PAUSED'


def test_collection_option_changes_use_existing_run(workflow):
    client, adapter, _, collection, create = workflow
    create().execute()
    create(replace(collection, save_log=False)).execute()
    assert client.posts == 1
    create(replace(collection, out=collection.out + '-other')).execute()
    assert client.posts == 1 and adapter.calls.count('log') == 2


def test_run_attempt_change_prevents_log_resume(workflow):
    client, adapter, store, _, create = workflow
    adapter.fail_kind = 'log'
    with pytest.raises(RecoverableError):
        create().execute()
    # The previous planned attempt stays required, so rerunning the same Run
    # cannot silently reuse its log bytes as another attempt's diagnostics.
    client.attempt = 2
    adapter.fail_kind = None
    with pytest.raises(ExecutionError):
        create().execute()
    assert store.load().phase == 'PAUSED'


def test_official_address_refresh_checks_artifact_metadata(workflow, monkeypatch):
    client, _, store, collection, create = workflow
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    urls = 0
    def address(endpoint):
        nonlocal urls
        urls += 1
        return f'https://download.invalid/{urls}?secret=SIGNED'
    client.download_address = address
    def probe(url, timeout):
        return HTTPMetadata(True, len(client.body), '"stable"')
    def runner(args, **kw):
        if urls == 1:
            return CommandResult(22)
        target = Path(collection.out) / '123/.ghwf-cache/artifact-7.zip'
        target.write_bytes(client.body)
        return CommandResult(0)
    adapter = DownloadAdapter(runner=runner, probe=probe)
    collector = ArtifactCollector(client, adapter)
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    c = ExecutionCoordinator(store, client, collector, identity,
                             replace(collection, save_log=False), {})
    assert c.execute().exit_code == 0 and urls == 2
    assert 'SIGNED' not in store.path.read_text()


def test_digest_failure_pauses_collection(workflow, monkeypatch):
    client, _, store, collection, _ = workflow
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    target = Path(collection.out) / '123/.ghwf-cache/artifact-7.zip'
    body = zip_bytes({'result.txt': 'tampered'})
    # Preserve a wrong digest, but make the declared size match this response.
    client.info = replace(client.info, size=len(body))
    def runner(args, **kw):
        target.write_bytes(body)
        return CommandResult(0)
    adapter = DownloadAdapter(runner=runner, probe=lambda *a: HTTPMetadata(True, len(body), '"v1"'))
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    c = ExecutionCoordinator(store, client, ArtifactCollector(client, adapter), identity,
                             replace(collection, save_log=False), {})
    with pytest.raises(RecoverableError, match='digest'):
        c.execute()
    assert store.load().phase == 'PAUSED' and not target.exists()


def test_github_process_and_redirect_are_injectable():
    calls = []
    def runner(args, **kw):
        calls.append((args, kw.get('stdin')))
        if args[1:3] == ['auth', 'token']:
            return CommandResult(0, 'SECRET_TOKEN')
        return CommandResult(0, 'HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n'
                             + json.dumps({'workflow_run_id': 123}))
    class Opener:
        def open(self, request, timeout):
            assert request.headers['Authorization'] == 'Bearer SECRET_TOKEN'
            headers = Message()
            headers['Location'] = 'https://blob.example/file?signature=PRIVATE'
            raise HTTPError(request.full_url, 302, 'redirect', headers, io.BytesIO())
    client = GitHubActionsClient(runner, opener=Opener())
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {'key': 'value'})
    assert client.dispatch(identity, {'key': 'value'}) == 123
    assert json.loads(calls[0][1])['return_run_details'] is True
    assert client.download_address('repos/owner/repo/actions/runs/123/logs').startswith('https://blob.example/')
    assert all('SECRET_TOKEN' not in ' '.join(args) for args, _ in calls)


def test_preflight_wrong_repo_ref_no_post():
    for failing in ('workflow', 'ref'):
        posts = []
        def runner(args, **kw):
            if 'POST' in args:
                posts.append(args)
            if failing == 'workflow' or '/git/ref/' in args[-1]:
                return CommandResult(1, stderr='HTTP 404')
            return CommandResult(0, '{"state": "active"}')
        client = GitHubActionsClient(runner)
        with pytest.raises(ExecutionError):
            client.validate(DispatchIdentity.create('owner/repo', 'ci.yml', 'bad', {}))
        assert not posts


def test_legacy_output_directory_can_be_upgraded(workflow, tmp_path):
    client, _, store, collection, create = workflow
    out = Path(collection.out) / '123'
    out.mkdir(parents=True)
    (out / 'run.log').write_text('original gh --log format')
    (out / 'result.txt').write_text('original gh artifact output')
    old = tmp_path / 'v1.json'
    atomic_json(old, {'version': 1, 'stage': 'finished', 'run_id': 123,
        'repo': 'owner/repo', 'workflow': 'ci.yml', 'ref': 'main', 'conclusion': 'success',
        'log_saved': True, 'artifacts_saved': True, 'artifact_files': ['result.txt']})
    assert create().execute(resume='last', legacy_paths=[old]).exit_code == 0
    assert client.posts == 0
    assert 'test error details' in (out / 'run.log').read_text()
    assert (out / 'result.txt').read_text() == 'build output'


def test_changed_artifact_metadata_blocks_expired_url_resume(workflow, monkeypatch):
    client, _, store, collection, _ = workflow
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    metadata_calls = []
    def metadata(repo, artifact_id):
        metadata_calls.append(artifact_id)
        return client.info if len(metadata_calls) == 1 else replace(client.info, size=client.info.size + 1)
    client.artifact = metadata
    calls = []
    def runner(args, **kw):
        calls.append(args)
        return CommandResult(22)
    adapter = DownloadAdapter(runner=runner,
        probe=lambda *a: HTTPMetadata(True, len(client.body), '"v1"'))
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    coordinator = ExecutionCoordinator(store, client, ArtifactCollector(client, adapter), identity,
                                       replace(collection, save_log=False), {})
    with pytest.raises(ResourceChanged, match='metadata changed'):
        coordinator.execute()
    assert len(calls) == 1 and client.posts == 1 and store.load().phase == 'PAUSED'


def test_artifact_cannot_collide_with_windows_cache_directory(workflow):
    client, _, store, collection, create = workflow
    client.info = replace(client.info, name='.GHWF-CACHE')
    with pytest.raises(ExecutionError, match='Unsafe artifact name'):
        create(replace(collection, artifact=None)).execute()
    assert store.load().phase == 'PAUSED'
