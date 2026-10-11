"""Optional aria2c and standard-library HTTP downloads with verified checkpoints."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, Request, build_opener, urlopen

from .config import DownloadConfig, DownloadConfigurationError
from .github import ProcessRunner, run_process
from .models import ExecutionError, RecoverableError
from .store import atomic_json
from .transport import DownloadProgress, ResourceIdentity, TransportResult


@dataclass(frozen=True)
class HTTPMetadata:
    range_supported: bool
    size: int | None
    validator: str | None


class DownloadAddressExpired(RecoverableError):
    pass


class ResourceChanged(ExecutionError):
    pass


class DownloadIntegrityError(RecoverableError):
    pass


class DownloadInterrupted(RecoverableError):
    def __init__(self, message: str, progress: DownloadProgress):
        self.progress = progress
        super().__init__(message)


def probe_http(url: str, timeout: int) -> HTTPMetadata:
    """Probe one byte for Range support without implementing a transfer engine."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise ExecutionError('Expected an HTTP(S) download URL without embedded credentials')
    try:
        with urlopen(Request(url, headers={'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'}),
                     timeout=timeout) as response:
            headers = response.headers
            content_range = headers.get('Content-Range', '')
            ranged = response.status == 206 and content_range.startswith('bytes 0-0/')
            size = content_range.rsplit('/', 1)[-1] if ranged else headers.get('Content-Length')
            # Weak validators cannot prove byte-for-byte resume consistency.
            etag = headers.get('ETag')
            validator = etag if etag and not etag.startswith('W/') else None
            if ranged:
                response.read(1)
            return HTTPMetadata(ranged, int(size) if size and size.isdigit() else None, validator)
    except HTTPError as exc:
        if exc.code in (401, 403, 404, 410):
            raise DownloadAddressExpired('Temporary download URL expired or unavailable') from exc
        raise RecoverableError(f'HTTP capability probe failed ({exc.code})') from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise DownloadInterrupted('HTTP capability probe interrupted', DownloadProgress(None, None, False)) from exc


def file_digest(path: Path) -> str:
    """Compute a streaming SHA256 for archive and extracted-file verification."""
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024**2), b''):
            digest.update(chunk)
        return digest.hexdigest()


def _cache_paths(target):
    return (target, target.with_name(target.name + '.aria2'),
            target.with_name(target.name + '.aria2__temp'),
            target.with_name(target.name + '.native.part'))


def _load_checkpoint(identity, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.with_name(target.name + '.resource.json')
    if any(p.is_symlink() for p in (*_cache_paths(target), marker)):
        raise ExecutionError('Download cache cannot contain symbolic links')
    if not marker.exists():
        if any(p.exists() for p in _cache_paths(target)):
            raise ResourceChanged('Partial file has no verified resource identity')
        return marker, None
    try:
        prior = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ResourceChanged('Unreadable download identity; refusing unsafe resume') from exc
    if not isinstance(prior, dict) or prior.get('resource') != asdict(identity):
        raise ResourceChanged('Remote resource identity changed; refusing prior partial data')
    return marker, prior


def _cached_result(target, prior):
    if prior and prior.get('complete') and target.is_file():
        if file_digest(target) == prior.get('sha256'):
            # Only verified complete files can be shared across backends.
            for path in _cache_paths(target)[1:]:
                path.unlink(missing_ok=True)
            size = target.stat().st_size
            return TransportResult(target, DownloadProgress(size, size, False))
        print('Download cache damaged; restarting completed file', file=sys.stderr)
        for path in _cache_paths(target):
            path.unlink(missing_ok=True)
    return None


def _restart(target, reason, recovery):
    if recovery == 'pause':
        raise RecoverableError(f'{reason}; restart disabled')
    print(f'Download restart: {reason}', file=sys.stderr)
    for path in _cache_paths(target):
        path.unlink(missing_ok=True)


def _check_metadata(prior, meta, identity):
    if prior and prior.get('validator') and meta.validator != prior['validator']:
        raise ResourceChanged('HTTP validator changed; refusing resume')
    if prior and prior.get('size') is not None and meta.size != prior['size']:
        raise ResourceChanged('Remote resource size changed; refusing resume')
    if identity.size is not None and meta.size is not None and identity.size != meta.size:
        raise ResourceChanged('Remote resource size differs from its declared identity')


def _verify_body(path, identity, expected_size):
    size = path.stat().st_size
    if expected_size is not None and size != expected_size:
        raise DownloadIntegrityError('Downloaded size differs from remote resource')
    sha = file_digest(path)
    if identity.digest:
        algorithm, _, expected = identity.digest.partition(':')
        if algorithm != 'sha256':
            raise ExecutionError(f'Unsupported remote digest algorithm: {algorithm}')
        if sha.lower() != expected.lower():
            path.unlink(missing_ok=True)
            raise DownloadIntegrityError('Artifact digest verification failed; cached body removed')
    return size, sha


class Aria2Backend:
    def __init__(self, config: DownloadConfig | None = None, runner: ProcessRunner = run_process,
                 probe=probe_http):
        self.config = config or DownloadConfig()
        self.runner, self.probe = runner, probe

    def download(self, resolve_url, identity: ResourceIdentity, target: Path) -> TransportResult:
        """Refresh expired addresses once; leave native progress on interruption."""
        if not shutil.which(self.config.executable):
            raise DownloadConfigurationError('aria2c not found; configure --aria2c or use --download-backend auto/native')
        marker, prior = _load_checkpoint(identity, target)
        control = target.with_name(target.name + '.aria2')
        cached = _cached_result(target, prior)
        if cached:
            return cached
        last_restart = None
        for refresh in range(2):
            url = resolve_url()
            try:
                meta = self.probe(url, self.config.connect_timeout)
                _check_metadata(prior, meta, identity)
                native_part = target.with_name(target.name + '.native.part')
                if native_part.exists() or (prior and prior.get('backend', 'aria2') != 'aria2'
                                           and any(p.exists() for p in _cache_paths(target))):
                    last_restart = 'Download backend changed; partial data cannot be reused'
                    _restart(target, last_restart, self.config.recovery)
                partial = target.exists() or control.exists()
                proven_identity = bool(identity.digest or identity.immutable or prior and prior.get('validator'))
                resumable = partial and control.exists() and meta.range_supported and proven_identity
                byte_resume = bool(meta.range_supported and (identity.digest or identity.immutable or meta.validator))
                print(f'Download byte resume: {"available" if byte_resume else "unavailable"}; '
                      f'resuming: {"yes" if resumable else "no"}', file=sys.stderr)
                if partial and not resumable:
                    last_restart = ('Source does not support HTTP Range' if not meta.range_supported
                                    else 'Native aria2 progress file is missing' if not control.exists()
                                    else 'Prior download has no strong validator or immutable resource identity')
                    if self.config.recovery == 'pause':
                        raise RecoverableError(f'{last_restart}; restart policy is disabled')
                    print(f'Download restart: {last_restart}', file=sys.stderr)
                    target.unlink(missing_ok=True)
                    control.unlink(missing_ok=True)
                checkpoint = {'backend': 'aria2', 'resource': asdict(identity), 'validator': meta.validator,
                              'size': meta.size, 'complete': False}
                atomic_json(marker, checkpoint)
                prior = checkpoint
                result = self._invoke(url, target, resumable, meta.range_supported)
                if result.returncode:
                    # Windows aria2 can close its native snapshot successfully but
                    # fail the final rename. Keep it opaque and let aria2 validate
                    # it on the next invocation; never promote a killed process's file.
                    native_temp = control.with_name(control.name + '__temp')
                    if (os.name == 'nt' and native_temp.is_file()
                            and 'DefaultBtProgressInfoFile.cc:222' in result.stdout + result.stderr):
                        os.replace(native_temp, control)
                    if result.returncode in (3, 22, 24):
                        raise DownloadAddressExpired('Download address failed; refresh before resuming')
                    raise DownloadInterrupted(
                        f'aria2c interrupted (exit {result.returncode}); native progress preserved',
                        DownloadProgress(None, meta.size, resumable, last_restart))
                if not target.is_file():
                    raise RecoverableError('aria2c did not produce a completed download')
                try:
                    size, sha = _verify_body(target, identity, meta.size if meta.size is not None else identity.size)
                except DownloadIntegrityError:
                    for path in _cache_paths(target):
                        path.unlink(missing_ok=True)
                    raise
                # Exit zero is aria2's completion contract. Some Windows builds
                # leave the final control file behind; remove it after verification.
                control.unlink(missing_ok=True)
                control.with_name(control.name + '__temp').unlink(missing_ok=True)
                atomic_json(marker, {**checkpoint, 'complete': True, 'sha256': sha})
                return TransportResult(target, DownloadProgress(size, meta.size, resumable, last_restart))
            except DownloadAddressExpired:
                if refresh == 1:
                    raise
        raise AssertionError('unreachable')

    def _invoke(self, url, target, resume, ranged):
        connections = self.config.connections if ranged else 1
        options = [self.config.executable, '--no-conf=true', '--continue=true' if resume else '--continue=false',
                   '--always-resume=true', '--auto-file-renaming=false', '--allow-overwrite=true',
                   f'--split={connections}', f'--max-connection-per-server={connections}',
                   '--min-split-size=1M', '--max-tries=1', '--retry-wait=0',
                   f'--timeout={self.config.timeout}', f'--connect-timeout={self.config.connect_timeout}',
                   '--auto-save-interval=1', '--file-allocation=none', '--http-accept-gzip=false',
                   '--console-log-level=warn', '--summary-interval=0', '--download-result=hide',
                   '--enable-color=false', '--input-file=-']
        # Signed URL is supplied over stdin, never in argv, logs or state.
        if '\n' in url or '\r' in url:
            raise ExecutionError('Invalid download URL')
        # Per-download options keep paths and the temporary URL off the command line.
        body = url + '\n  dir=' + str(target.parent) + '\n  out=' + target.name + '\n'
        try:
            return self.runner(options, stdin=body, timeout=self.config.process_timeout)
        except FileNotFoundError as exc:
            raise DownloadConfigurationError('aria2c disappeared; check --aria2c configuration') from exc
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise DownloadInterrupted('aria2c process interrupted; progress preserved',
                                      DownloadProgress(None, None, resume)) from exc


def _open_native(url, config):
    # urllib retains its redirect, TLS verification and proxy support. Connections
    # use one timeout for connecting (including TLS) and another for reading.
    class HttpConnection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            self.sock.settimeout(config.timeout)

    class HttpsConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            self.sock.settimeout(config.timeout)

    class HttpHandler(HTTPHandler):
        def http_open(self, request):
            return self.do_open(HttpConnection, request)

    class HttpsHandler(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(HttpsConnection, request, context=self._context)

    return build_opener(HttpHandler(), HttpsHandler()).open(
        Request(url, headers={'Accept-Encoding': 'identity'}), timeout=config.connect_timeout)


class NativeHttpBackend:
    def __init__(self, config: DownloadConfig, opener=_open_native):
        self.config, self.opener = config, opener

    def download(self, resolve_url, identity: ResourceIdentity, target: Path) -> TransportResult:
        marker, prior = _load_checkpoint(identity, target)
        cached = _cached_result(target, prior)
        if cached:
            return cached
        temporary = target.with_name(target.name + '.native.part')
        last_restart = None
        received, total = 0, None
        for refresh in range(2):
            url = resolve_url()
            parsed = urlsplit(url)
            if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
                    or '\n' in url or '\r' in url):
                raise ExecutionError('Expected an HTTP(S) download URL without embedded credentials')
            try:
                with self.opener(url, self.config) as response:
                    if response.status != 200:
                        raise DownloadIntegrityError('Expected a complete HTTP 200 response for native download')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise DownloadIntegrityError('Unexpected encoded HTTP download body')
                    length = response.headers.get('Content-Length', '')
                    total = int(length) if length.isdigit() else None
                    etag = response.headers.get('ETag')
                    meta = HTTPMetadata(False, total, etag if etag and not etag.startswith('W/') else None)
                    _check_metadata(prior, meta, identity)
                    if any(p.exists() for p in _cache_paths(target)):
                        last_restart = ('Download backend changed; partial data cannot be reused'
                                        if prior and prior.get('backend', 'aria2') != 'native'
                                        else 'Native HTTP does not support byte resume; restarting unfinished file')
                        _restart(target, last_restart, self.config.recovery)
                    checkpoint = {'backend': 'native', 'resource': asdict(identity),
                                  'validator': meta.validator, 'size': total, 'complete': False}
                    atomic_json(marker, checkpoint)
                    prior = checkpoint
                    received = 0
                    started = time.monotonic()
                    with temporary.open('wb') as stream:
                        while True:
                            if time.monotonic() - started > self.config.process_timeout:
                                raise TimeoutError('Download time limit exceeded')
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            stream.write(chunk)
                            received += len(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if total is not None and received < total:
                        raise DownloadInterrupted('HTTP download interrupted; partial file preserved',
                                                  DownloadProgress(received, total, False, last_restart))
                    size, sha = _verify_body(temporary, identity, total if total is not None else identity.size)
                    os.replace(temporary, target)
                    atomic_json(marker, {**checkpoint, 'complete': True, 'sha256': sha})
                    return TransportResult(target, DownloadProgress(size, total, False, last_restart))
            except HTTPError as exc:
                if exc.code in (401, 403, 404, 410):
                    if refresh == 0:
                        continue
                    raise DownloadAddressExpired('Temporary URL expired or access denied') from None
                raise RecoverableError(f'HTTP download failed ({exc.code})') from None
            except (URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
                raise DownloadInterrupted('HTTP download interrupted; partial file preserved',
                                          DownloadProgress(received, total, False, last_restart)) from exc
        raise AssertionError('unreachable')


class DownloadAdapter:
    """Select a download backend without exposing it to workflow execution."""
    def __init__(self, config: DownloadConfig | None = None, runner: ProcessRunner = run_process,
                 probe=probe_http, opener=_open_native):
        self.config = config or DownloadConfig()
        self.runner, self.probe, self.opener = runner, probe, opener

    def download(self, resolve_url, identity: ResourceIdentity, target: Path) -> TransportResult:
        available = shutil.which(self.config.executable) if self.config.backend != 'native' else None
        backend = self.config.backend
        if backend == 'auto':
            backend = 'aria2' if available else 'native'
            if not available:
                print('aria2c unavailable; auto fallback to native HTTP', file=sys.stderr)
        if backend == 'aria2':
            if not available:
                raise DownloadConfigurationError('aria2c not found; configure --aria2c or use --download-backend auto/native')
            print('Download backend: aria2 (byte resume requires HTTP Range and verified native progress)',
                  file=sys.stderr)
            return Aria2Backend(self.config, self.runner, self.probe).download(resolve_url, identity, target)
        print('Download backend: native (single connection; byte resume unsupported)', file=sys.stderr)
        return NativeHttpBackend(self.config, self.opener).download(resolve_url, identity, target)
