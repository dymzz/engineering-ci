"""aria2c downloads with native checkpoints and safe HTTP capability checks."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .github import ProcessRunner, run_process
from .models import ExecutionError, RecoverableError
from .store import atomic_json
from .transport import DownloadProgress, ResourceIdentity, TransportResult


@dataclass(frozen=True)
class DownloadConfig:
    connections: int = 4
    timeout: int = 30
    connect_timeout: int = 15
    process_timeout: int = 1800
    recovery: str = 'restart'
    executable: str = 'aria2c'

    def __post_init__(self):
        if not 1 <= self.connections <= 16 or min(self.timeout, self.connect_timeout, self.process_timeout) <= 0:
            raise ValueError('Download connections must be 1..16 and timeouts must be positive')
        if self.recovery not in ('restart', 'pause'):
            raise ValueError('Download recovery must be restart or pause')


@dataclass(frozen=True)
class HTTPMetadata:
    range_supported: bool
    size: int | None
    validator: str | None


class DownloadAddressExpired(RecoverableError):
    pass


class ResourceChanged(ExecutionError):
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
        raise RecoverableError('HTTP capability probe interrupted') from exc


def file_digest(path: Path) -> str:
    """Compute a streaming SHA256 for archive and extracted-file verification."""
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024**2), b''):
            digest.update(chunk)
        return digest.hexdigest()


class DownloadAdapter:
    def __init__(self, config: DownloadConfig | None = None, runner: ProcessRunner = run_process,
                 probe=probe_http):
        self.config = config or DownloadConfig()
        self.runner, self.probe = runner, probe

    def download(self, resolve_url, identity: ResourceIdentity, target: Path) -> TransportResult:
        """Refresh expired addresses once; leave native progress on interruption."""
        if not shutil.which(self.config.executable):
            raise ExecutionError('aria2c not found; install it or specify --aria2c')
        target.parent.mkdir(parents=True, exist_ok=True)
        marker = target.with_name(target.name + '.resource.json')
        control = target.with_name(target.name + '.aria2')
        if any(p.is_symlink() for p in (target, marker, control, control.with_name(control.name + '__temp'))):
            raise ExecutionError('Download cache cannot contain symbolic links')
        prior = None
        if marker.exists():
            try:
                prior = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ResourceChanged('Unreadable download identity; refusing unsafe resume') from exc
            if prior.get('resource') != asdict(identity):
                raise ResourceChanged('Remote resource identity changed; refusing prior partial data')
        elif target.exists() or control.exists():
            raise ResourceChanged('Partial file has no verified resource identity')
        before = target.stat().st_size if target.exists() else 0
        if prior and prior.get('complete') and target.is_file() and not control.exists():
            if file_digest(target) == prior.get('sha256'):
                return TransportResult(target, DownloadProgress(before, before, False))
            # Locally corrupted complete data can be safely replaced in this owned cache.
            target.unlink()
            before = 0
        last_restart = None
        for refresh in range(2):
            url = resolve_url()
            try:
                meta = self.probe(url, self.config.connect_timeout)
                if prior and prior.get('validator') and meta.validator != prior['validator']:
                    raise ResourceChanged('HTTP validator changed; refusing resume')
                if prior and prior.get('size') is not None and meta.size != prior['size']:
                    raise ResourceChanged('Remote download size changed; refusing resume')
                partial = target.exists() or control.exists()
                proven_identity = bool(identity.digest or identity.immutable or prior and prior.get('validator'))
                resumable = partial and control.exists() and meta.range_supported and proven_identity
                if partial and not resumable:
                    last_restart = ('Source does not support HTTP Range' if not meta.range_supported
                                    else 'Native aria2 progress file is missing' if not control.exists()
                                    else 'Prior download has no strong validator or immutable resource identity')
                    if self.config.recovery == 'pause':
                        raise RecoverableError(f'{last_restart}; restart policy is disabled')
                    print(f'Download restart: {last_restart}', file=sys.stderr)
                    target.unlink(missing_ok=True)
                    control.unlink(missing_ok=True)
                checkpoint = {'resource': asdict(identity), 'validator': meta.validator,
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
                size = target.stat().st_size
                if meta.size is not None and size != meta.size:
                    raise RecoverableError('Downloaded size differs from the remote resource')
                sha = file_digest(target)
                if identity.digest:
                    algorithm, _, expected = identity.digest.partition(':')
                    if algorithm != 'sha256':
                        raise ExecutionError(f'Unsupported remote digest algorithm: {algorithm}')
                    if sha.lower() != expected.lower():
                        target.unlink()
                        control.unlink(missing_ok=True)
                        control.with_name(control.name + '__temp').unlink(missing_ok=True)
                        raise RecoverableError('Artifact digest verification failed; cached body removed')
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
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise RecoverableError('aria2c process interrupted; progress preserved') from exc
