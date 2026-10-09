"""P0-1: legal state changes, durable intent and recovery without network."""
import json
import multiprocessing
from dataclasses import replace
from pathlib import Path

import pytest

from engineering_ci.execution.coordinator import ExecutionCoordinator
from engineering_ci.execution.fsm import ExecutionFSM, InvalidTransition
from engineering_ci.execution.github import CommandResult, GitHubActionsClient
from engineering_ci.execution.models import (CollectionIdentity, DispatchIdentity,
    DispatchRejected, DispatchUnknown, ExecutionError, RecoverableError, TaskRecord)
from engineering_ci.execution.store import StateStore, atomic_json


class FakeClient:
    def __init__(self):
        self.posts = 0
        self.failure = None
        self.wait_failure = None
        self.conclusion = 'success'
        self.on_dispatch = lambda: None
    def validate(self, identity):
        if identity.repo == 'bad/repo' or identity.ref == 'bad':
            raise ExecutionError('Invalid target')
    def dispatch(self, identity, inputs):
        self.posts += 1
        self.on_dispatch()
        if self.failure:
            raise self.failure
        return 123
    def get_run(self, identity, run_id):
        return {'id': run_id, 'status': 'completed', 'conclusion': self.conclusion}
    def wait(self, identity, run_id, timeout, interval):
        if self.wait_failure:
            raise self.wait_failure
        return self.get_run(identity, run_id)


class FakeCollector:
    def __init__(self):
        self.calls = 0
        self.done = False
        self.failure = None
    def intact(self, record):
        return self.done
    def collect(self, record, checkpoint):
        self.calls += 1
        if self.failure:
            raise self.failure
        self.done = True
        checkpoint(files={'collected': True})


@pytest.fixture
def setup(tmp_path):
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    store = StateStore(tmp_path / 'state.json')
    client, collector = FakeClient(), FakeCollector()
    collection = CollectionIdentity.create(tmp_path / 'out', save_log=True)
    def coordinator(**kw):
        return ExecutionCoordinator(store, client, collector, kw.get('identity', identity),
                                    kw.get('collection', collection), {})
    return store, client, collector, coordinator


def test_normal_and_successful_new_invocation(setup):
    store, client, collector, create = setup
    assert create().execute().exit_code == 0
    assert store.load().phase == 'FINISHED'
    assert create().execute(resume='last').exit_code == 0
    assert client.posts == 1
    create().execute()
    assert client.posts == 2


@pytest.mark.parametrize('repo,ref', [('bad/repo', 'main'), ('owner/repo', 'bad')])
def test_preflight_leaves_no_state(setup, repo, ref):
    store, client, _, create = setup
    with pytest.raises(ExecutionError):
        create(identity=DispatchIdentity.create(repo, 'ci.yml', ref, {})).execute()
    assert client.posts == 0 and not store.path.exists()


def test_422_rejected_can_retry(setup):
    store, client, _, create = setup
    client.failure = DispatchRejected(422)
    with pytest.raises(DispatchRejected):
        create().execute()
    assert store.load().phase == 'REJECTED'
    client.failure = None
    create().execute()
    assert client.posts == 2


def test_response_loss_never_reposts(setup):
    store, client, _, create = setup
    def check_intent():
        assert store.load().phase == 'DISPATCHING'
    client.on_dispatch = check_intent
    client.failure = DispatchUnknown('response lost')
    for _ in range(2):
        with pytest.raises(DispatchUnknown):
            create().execute()
    assert store.load().phase == 'UNKNOWN' and client.posts == 1
    client.failure = None
    create().execute(resume='123')
    assert client.posts == 1


def test_crash_after_run_id_restores_same_run(setup):
    store, client, _, create = setup
    def crash(*args):
        assert store.load().run_id == 123
        raise SystemExit(9)
    old_wait = client.wait
    client.wait = crash
    with pytest.raises(SystemExit):
        create().execute()
    assert store.load().phase == 'RUNNING'
    client.wait = old_wait
    create().execute()
    assert client.posts == 1


def test_tls_wait_pause_and_collection_pause(setup):
    store, client, collector, create = setup
    client.wait_failure = RecoverableError('TLS timeout')
    with pytest.raises(RecoverableError):
        create().execute()
    assert store.load().paused_from == 'RUNNING'
    client.wait_failure = None
    collector.failure = RecoverableError('Interrupted log')
    with pytest.raises(RecoverableError):
        create().execute()
    assert store.load().paused_from == 'COLLECTING'
    client.wait_failure = AssertionError('Must not poll again')
    collector.failure = None
    create().execute()
    assert client.posts == 1


def test_failure_still_collects_and_exits_nonzero(setup):
    store, client, collector, create = setup
    client.conclusion = 'failure'
    assert create().execute().exit_code == 1
    assert collector.calls == 1 and store.load().collection_complete
    create().execute()
    assert client.posts == 1


def test_changing_collection_does_not_dispatch(setup, tmp_path):
    _, client, _, create = setup
    create().execute()
    create(collection=CollectionIdentity.create(tmp_path / 'different', save_log=False)).execute()
    assert client.posts == 1


def test_illegal_fsm_transitions(setup):
    _, _, _, create = setup
    record = TaskRecord(create().identity, create().collection)
    fsm = ExecutionFSM()
    for event in ('finish', 'dispatch', 'to_FINISHED', 'checkpoint'):
        with pytest.raises(InvalidTransition):
            fsm.advance(record, event)
    with pytest.raises(InvalidTransition):
        fsm.advance(record, 'validate', phase='FINISHED')
    running = fsm.advance(record, 'attach_run', run_id=123)
    paused = fsm.advance(running, 'pause')
    with pytest.raises(InvalidTransition, match='paused phase'):
        fsm.advance(paused, 'resume_collecting')
    with pytest.raises(InvalidTransition, match='Fields cannot'):
        fsm.advance(running, 'checkpoint', run_id=999)


def test_old_state_migration(setup, tmp_path):
    store, client, _, create = setup
    c = create()
    old = tmp_path / 'old.json'
    atomic_json(old, {'version': 1, 'repo': 'owner/repo', 'workflow': 'ci.yml',
                     'ref': 'main', 'stage': 'dispatched', 'run_id': 123})
    c.execute(legacy_paths=[old])
    assert client.posts == 0 and store.load().schema_version == 2
    assert json.loads(old.read_text())['version'] == 1


def test_atomic_failure_preserves_checkpoint(tmp_path, monkeypatch):
    import engineering_ci.execution.store as module
    path = tmp_path / 'state.json'
    atomic_json(path, {'old': True})
    monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        atomic_json(path, {'new': True})
    assert json.loads(path.read_text()) == {'old': True}
    assert not list(tmp_path.glob('*.tmp'))


def _lock_holder(path, ready, release):
    with StateStore(Path(path)).lock():
        ready.set()
        release.wait(15)


def test_two_processes_excluded(setup):
    store, client, _, create = setup
    context = multiprocessing.get_context('spawn')
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_lock_holder, args=(str(store.path), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(ExecutionError, match='another ghwf process'):
            create().execute()
        assert client.posts == 0
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()


def test_dispatch_cli_error_classification():
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {})
    for status, expected in [(422, DispatchRejected), (503, DispatchUnknown)]:
        calls = []
        def runner(args, **kw):
            calls.append(args)
            return CommandResult(1, stderr=f'HTTP {status} TLS timeout')
        with pytest.raises(expected):
            GitHubActionsClient(runner).dispatch(identity, {})
        assert len(calls) == 1


def test_failed_run_id_persistence_prints_recovery_id(setup, monkeypatch, capsys):
    store, client, _, create = setup
    save = store.save
    def fail_attach(record):
        if record.phase == 'RUNNING':
            raise OSError('disk full')
        save(record)
    monkeypatch.setattr(store, 'save', fail_attach)
    with pytest.raises(OSError):
        create().execute()
    assert 'Run ID: 123' in capsys.readouterr().out
    assert store.load().phase == 'DISPATCHING' and client.posts == 1


def _execute_in_process(path, out, posts, ready, release, results):
    client = FakeClient()
    def post():
        with posts.get_lock():
            posts.value += 1
        ready.set()
        release.wait(15)
    client.on_dispatch = post
    coordinator = ExecutionCoordinator(StateStore(Path(path)), client, FakeCollector(),
        DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {}),
        CollectionIdentity.create(Path(out)), {})
    try:
        coordinator.execute()
        results.put('finished')
    except ExecutionError:
        results.put('excluded')


def test_two_coordinators_only_one_dispatch(tmp_path):
    context = multiprocessing.get_context('spawn')
    posts = context.Value('i', 0)
    ready, release, results = context.Event(), context.Event(), context.Queue()
    args = (str(tmp_path / 'state.json'), str(tmp_path / 'out'), posts, ready, release, results)
    first = context.Process(target=_execute_in_process, args=args)
    second = context.Process(target=_execute_in_process, args=args)
    first.start()
    try:
        assert ready.wait(10)
        second.start()
        assert results.get(timeout=10) == 'excluded'
        release.set()
        assert results.get(timeout=10) == 'finished'
        assert posts.value == 1
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(10)
                if process.is_alive():
                    process.terminate()
