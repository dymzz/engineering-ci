"""P0-1: legal state changes, durable intent and recovery without network."""
import json
import multiprocessing
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from engineering_ci.execution.coordinator import ExecutionCoordinator
from engineering_ci.execution.fsm import ExecutionFSM, InvalidTransition
from engineering_ci.execution.github import CommandResult, GitHubActionsClient
from engineering_ci.execution.models import (CollectionIdentity, DispatchErrorInfo, DispatchErrorReason, DispatchIdentity,
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


def _http_dispatch_response(status, body='', *, returncode=0, version='2.0', separator='\r\n'):
    headers = (f'HTTP/{version} {status} response', 'Content-Type: application/json',
               'X-Diagnostic: HEADER_SECRET')
    raw = separator.join(headers) + separator * 2 + body
    return CommandResult(returncode, raw)


@pytest.mark.parametrize('version,separator', [('1.1', '\r\n'), ('2.0', '\r\n'), ('2', '\n')])
def test_dispatch_http_200_valid_run_id(version, separator):
    calls = []
    def runner(args, **kw):
        calls.append((args, kw['stdin']))
        return _http_dispatch_response(200, '{"workflow_run_id": 123}',
                                       version=version, separator=separator)
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {'token': 'INPUT_SECRET'})
    assert GitHubActionsClient(runner).dispatch(identity, {'token': 'INPUT_SECRET'}) == 123
    assert len(calls) == 1 and '--include' in calls[0][0] and 'POST' in calls[0][0]
    assert 'INPUT_SECRET' not in ' '.join(calls[0][0])
    assert json.loads(calls[0][1])['return_run_details'] is True


@pytest.mark.parametrize('response,reason,status', [
    (_http_dispatch_response(204), DispatchErrorReason.ACCEPTED_WITHOUT_RUN_ID, 204),
    (_http_dispatch_response(200), DispatchErrorReason.EMPTY_RESPONSE, 200),
    (_http_dispatch_response(200, '{"token": "BODY_SECRET",'), DispatchErrorReason.INVALID_JSON, 200),
    (_http_dispatch_response(200, '[]'), DispatchErrorReason.UNEXPECTED_RESPONSE_TYPE, 200),
    (_http_dispatch_response(200, 'null'), DispatchErrorReason.UNEXPECTED_RESPONSE_TYPE, 200),
    (_http_dispatch_response(200, '"BODY_SECRET"'), DispatchErrorReason.UNEXPECTED_RESPONSE_TYPE, 200),
    (_http_dispatch_response(200, '{"token":"BODY_SECRET"}'), DispatchErrorReason.MISSING_RUN_ID, 200),
    (_http_dispatch_response(200, '{"workflow_run_id":true}'), DispatchErrorReason.INVALID_RUN_ID, 200),
    (_http_dispatch_response(200, '{"workflow_run_id":0}'), DispatchErrorReason.INVALID_RUN_ID, 200),
    (_http_dispatch_response(200, '{"workflow_run_id":-1}'), DispatchErrorReason.INVALID_RUN_ID, 200),
    (_http_dispatch_response(200, '{"workflow_run_id":"123"}'), DispatchErrorReason.INVALID_RUN_ID, 200),
    (_http_dispatch_response(200, '{"workflow_run_id":null}'), DispatchErrorReason.INVALID_RUN_ID, 200),
    (_http_dispatch_response(503, '{"message":"BODY_SECRET"}', returncode=1), DispatchErrorReason.HTTP_ERROR, 503),
    (CommandResult(0, '{"workflow_run_id":123,"note":"HTTP 422 BODY_SECRET"}'),
     DispatchErrorReason.MISSING_HTTP_STATUS, None),
    (CommandResult(0, 'HTTP/2.0 200 OK\r\nX-Diagnostic: HEADER_SECRET\r\n'),
     DispatchErrorReason.MALFORMED_HTTP_RESPONSE, 200),
    (CommandResult(1, stderr='gh: TLS handshake timeout STDERR_SECRET'),
     DispatchErrorReason.NETWORK_TIMEOUT, None),
    (CommandResult(1, stderr='gh: unexpected EOF STDERR_SECRET'),
     DispatchErrorReason.NETWORK_INTERRUPTED, None),
    (CommandResult(1, 'HTTP/2.0 200 OK\r\n\r\n{"workflow_run_id":', 'unexpected EOF STDERR_SECRET'),
     DispatchErrorReason.NETWORK_INTERRUPTED, 200),
])
def test_dispatch_unknown_retains_safe_diagnostic_and_never_reposts(setup, response, reason, status):
    store, _, collector, create = setup
    posts = []
    def runner(args, **kw):
        if 'POST' in args:
            posts.append(args)
            return response
        return CommandResult(0, '{"state":"active"}')
    client = GitHubActionsClient(runner)
    first_details = None
    for attempt in range(2):
        coordinator = create()
        coordinator.client = client
        with pytest.raises(DispatchUnknown) as caught:
            coordinator.execute()
        details = caught.value.details
        assert caught.value.http_status == status and caught.value.reason == reason
        if attempt == 0:
            first_details = details
        else:
            assert details == first_details
        state = store.load()
        assert state.phase == 'UNKNOWN' and state.run_id is None
        diagnostic = json.loads(state.last_error)
        assert diagnostic['reason'] == reason.value and diagnostic['http_status'] == status
        assert diagnostic['process_exit_code'] == response.returncode
        assert DispatchErrorInfo.from_state_error(state.last_error) == details
        for secret in ('BODY_SECRET', 'HEADER_SECRET', 'STDERR_SECRET'):
            assert secret not in store.path.read_text() and secret not in str(caught.value)
    assert len(posts) == 1 and collector.calls == 0


@pytest.mark.parametrize('exception,reason', [
    (subprocess.TimeoutExpired(['gh', 'api'], 180, output='OUTPUT_SECRET', stderr='STDERR_SECRET'),
     DispatchErrorReason.NETWORK_TIMEOUT),
    (ConnectionResetError('connection reset TOKEN_SECRET'), DispatchErrorReason.NETWORK_INTERRUPTED),
    (OSError('TLS handshake timeout TOKEN_SECRET'), DispatchErrorReason.NETWORK_TIMEOUT),
    (FileNotFoundError('PATH_SECRET'), DispatchErrorReason.COMMAND_FAILED),
])
def test_dispatch_process_interruption_is_unknown(setup, exception, reason):
    store, _, _, create = setup
    posts = []
    def runner(args, **kw):
        if 'POST' in args:
            posts.append(args)
            raise exception
        return CommandResult(0, '{"state":"active"}')
    client = GitHubActionsClient(runner)
    for _ in range(2):
        coordinator = create()
        coordinator.client = client
        with pytest.raises(DispatchUnknown) as caught:
            coordinator.execute()
        assert caught.value.reason == reason
        assert caught.value.http_status is None
        assert json.loads(store.load().last_error)['reason'] == reason.value
        assert 'SECRET' not in store.path.read_text() and 'SECRET' not in str(caught.value)
    assert len(posts) == 1


@pytest.mark.parametrize('message,reason', [
    ('Validation Failed TOKEN_SECRET', DispatchErrorReason.HTTP_REJECTED),
    ('No ref found for: REF_SECRET', DispatchErrorReason.INVALID_REF),
    ("Workflow does not have 'workflow_dispatch' trigger TOKEN_SECRET",
     DispatchErrorReason.WORKFLOW_NOT_DISPATCHABLE),
])
def test_dispatch_422_retains_safe_rejection_and_can_retry(setup, message, reason):
    store, _, _, create = setup
    posts = []
    responses = iter([
        _http_dispatch_response(422, json.dumps({'message': message, 'errors': ['BODY_SECRET']}), returncode=1),
        _http_dispatch_response(200, '{"workflow_run_id":123}'),
    ])
    def runner(args, **kw):
        if 'POST' in args:
            posts.append(args)
            return next(responses)
        if '/actions/runs/' in args[-1]:
            return CommandResult(0, '{"id":123,"status":"completed","conclusion":"success"}')
        return CommandResult(0, '{"state":"active"}')
    client = GitHubActionsClient(runner)
    coordinator = create()
    coordinator.client = client
    with pytest.raises(DispatchRejected) as caught:
        coordinator.execute()
    assert caught.value.status == caught.value.http_status == 422
    assert caught.value.reason == reason
    assert store.load().phase == 'REJECTED' and store.load().http_status == 422
    diagnostic = json.loads(store.load().last_error)
    assert diagnostic['reason'] == reason.value and diagnostic['http_status'] == 422
    assert 'SECRET' not in store.path.read_text() and 'SECRET' not in str(caught.value)
    retry = create()
    retry.client = client
    assert retry.execute().exit_code == 0 and len(posts) == 2


def test_dispatch_200_body_cannot_spoof_rejection():
    response = _http_dispatch_response(200, '{"message":"HTTP 422 BODY_SECRET"}')
    client = GitHubActionsClient(lambda *a, **kw: response)
    with pytest.raises(DispatchUnknown) as caught:
        client.dispatch(DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {}), {})
    assert caught.value.http_status == 200
    assert caught.value.reason == DispatchErrorReason.MISSING_RUN_ID


def test_dispatch_info_ignores_untrusted_stored_message():
    diagnostic = json.dumps({'reason': 'invalid_json', 'http_status': 200,
                             'process_exit_code': 0, 'safe_message': 'TOKEN_SECRET'})
    details = DispatchErrorInfo.from_state_error(diagnostic)
    assert details.reason == DispatchErrorReason.INVALID_JSON
    assert 'TOKEN_SECRET' not in details.to_state_error()
    assert 'TOKEN_SECRET' not in str(DispatchUnknown('TOKEN_SECRET', details=details))


def test_dispatch_informational_headers_do_not_hide_final_status():
    response = _http_dispatch_response(200, '{"workflow_run_id":123}')
    response = CommandResult(0, 'HTTP/1.1 100 Continue\r\n\r\n' + response.stdout)
    client = GitHubActionsClient(lambda *a, **kw: response)
    assert client.dispatch(DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {}), {}) == 123


def test_dispatch_known_run_id_survives_cli_error_after_response():
    response = _http_dispatch_response(200, '{"workflow_run_id":123}', returncode=1)
    response = CommandResult(1, response.stdout, 'unexpected EOF STDERR_SECRET')
    client = GitHubActionsClient(lambda *a, **kw: response)
    assert client.dispatch(DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {}), {}) == 123


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
