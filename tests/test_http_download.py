"""P0-2: real aria2 against local HTTP servers; no GitHub calls."""
import hashlib
import json
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from engineering_ci import checks
from engineering_ci.execution.download import (DownloadAdapter, DownloadConfig,
    DownloadAddressExpired, HTTPMetadata, RecoverableError, ResourceChanged)
from engineering_ci.execution.github import CommandResult
from engineering_ci.execution.transport import ResourceIdentity


def aria2_executable(request):
    found = checks.aria2_executable(Path(__file__).resolve().parents[1])
    if found:
        return found
    spec = checks.PIPELINE['optional_backends']['aria2']
    request.node.user_properties.append((spec['junit_property'], spec['junit_value']))
    pytest.skip('Optional aria2 backend integration is not enabled; native and auto fallback tests still run')


@contextmanager
def http_server(*, ranged=True, slow=True):
    payload = bytes(range(256)) * (32 * 1024)  # 8 MiB, enough for native progress snapshots.
    requests = []
    interrupt = [True]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requested = self.headers.get('Range')
            requests.append(requested)
            start, end = 0, len(payload) - 1
            if requested and ranged:
                spec = requested.removeprefix('bytes=')
                first, last = spec.split('-')
                start = int(first)
                end = min(int(last), end) if last else end
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(payload)}')
            else:
                self.send_response(200)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('ETag', '"stable-fixture-v1"')
            self.end_headers()
            try:
                for offset in range(start, end + 1, 16384):
                    if interrupt[0] and end > start and offset - start >= 2 * 1024**2:
                        self.close_connection = True
                        return
                    self.wfile.write(payload[offset:min(offset + 16384, end + 1)])
                    self.wfile.flush()
                    if slow and end > start:
                        time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/file.zip', payload, requests, interrupt
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize('ranged', [True, False])
def test_native_progress_resume_and_range_fallback(tmp_path, ranged, request):
    config = DownloadConfig(connections=2, executable=aria2_executable(request), process_timeout=30, backend='aria2')
    target = tmp_path / 'download.zip'
    adapter = DownloadAdapter(config)
    with http_server(ranged=ranged) as (url, payload, requests, interrupt):
        identity = ResourceIdentity('fixture:1', digest='sha256:' + hashlib.sha256(payload).hexdigest())
        with pytest.raises(RecoverableError, match='interrupted'):
            adapter.download(lambda: url, identity, target)
        assert target.with_name('download.zip.aria2').is_file()
        assert not json.loads(target.with_name('download.zip.resource.json').read_text())['complete']
        cut = len(requests)
        interrupt[0] = False
        # New adapter object represents a process restart.
        result = DownloadAdapter(config).download(lambda: url, identity, target)
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(payload).digest()
        assert not target.with_name('download.zip.aria2').exists()
        assert result.progress.resumed is ranged
        later_ranges = [r for r in requests[cut:] if r and r != 'bytes=0-0']
        if ranged:
            assert any(int(r[6:].split('-')[0]) > 0 for r in later_ranges)
        else:
            assert result.progress.restart_reason == 'Source does not support HTTP Range'
            assert not later_ranges


def test_expired_url_refresh_and_identity_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    urls = iter(['expired', 'current'])
    target = tmp_path / 'file.zip'
    def probe(url, timeout):
        if url == 'expired':
            raise DownloadAddressExpired('expired')
        return HTTPMetadata(True, 2, '"v1"')
    def runner(args, **kw):
        target.write_bytes(b'OK')
        return CommandResult(0)
    adapter = DownloadAdapter(runner=runner, probe=probe)
    adapter.download(lambda: next(urls), ResourceIdentity('artifact:1'), target)
    with pytest.raises(ResourceChanged):
        adapter.download(lambda: 'current', ResourceIdentity('artifact:2'), target)
    state = target.with_name('file.zip.resource.json').read_text()
    assert 'expired' not in state and 'current' not in state


def test_validator_change_blocks_partial_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    target = tmp_path / 'file.zip'
    def interrupt(args, **kw):
        target.write_bytes(b'partial')
        target.with_name('file.zip.aria2').write_bytes(b'progress')
        return CommandResult(1)
    adapter = DownloadAdapter(runner=interrupt, probe=lambda *a: HTTPMetadata(True, 100, '"v1"'))
    with pytest.raises(RecoverableError):
        adapter.download(lambda: 'first', ResourceIdentity('artifact:1'), target)
    adapter.probe = lambda *a: HTTPMetadata(True, 100, '"v2"')
    with pytest.raises(ResourceChanged, match='validator'):
        adapter.download(lambda: 'second', ResourceIdentity('artifact:1'), target)
    assert target.read_bytes() == b'partial'


def test_no_validator_requires_explicit_downgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda exe: exe)
    target = tmp_path / 'file.zip'
    def interrupt(args, **kw):
        target.write_bytes(b'partial')
        target.with_name('file.zip.aria2').write_bytes(b'native-progress')
        assert '--max-tries=1' in args
        assert not any('signed' in a for a in args)
        return CommandResult(1)
    adapter = DownloadAdapter(DownloadConfig(recovery='pause'), runner=interrupt,
                               probe=lambda *a: HTTPMetadata(True, 100, None))
    identity = ResourceIdentity('mutable-resource')
    with pytest.raises(RecoverableError):
        adapter.download(lambda: 'https://test.invalid/?signed=SECRET', identity, target)
    with pytest.raises(RecoverableError, match='no strong validator'):
        adapter.download(lambda: 'https://test.invalid/?signed=SECRET', identity, target)
    assert target.read_bytes() == b'partial'
