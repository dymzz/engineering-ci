"""GitHub CLI operations with single-attempt side effects."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

from .models import (DispatchIdentity, DispatchRejected, DispatchUnknown,
                     ExecutionError, RecoverableError)
from .transport import ResourceIdentity

REJECTED_STATUSES = {400, 401, 403, 404, 405, 409, 410, 415, 422}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''


class ProcessRunner(Protocol):
    def __call__(self, args: list[str], *, stdin: str | None = None,
                 timeout: float = 180) -> CommandResult: ...


def run_process(args: list[str], *, stdin=None, timeout=180) -> CommandResult:
    """Run a native tool with captured output and no shell expansion."""
    result = subprocess.run(args, input=stdin, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout, check=False)
    return CommandResult(result.returncode, result.stdout, result.stderr)


class GhCommandError(ExecutionError):
    def __init__(self, status: int | None):
        self.http_status = status
        super().__init__(f'GitHub CLI failed (HTTP {status or "unknown"})')


@dataclass(frozen=True)
class ArtifactInfo:
    id: int
    name: str
    size: int
    digest: str | None = None
    expired: bool = False

    def identity(self, repo: str, run_id: int) -> ResourceIdentity:
        return ResourceIdentity(f'artifact:{repo}:{run_id}:{self.id}', self.size, self.digest, True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gh(*args: str, stdin=None, command_timeout=180) -> str:
    """Compatibility bridge for preserved repository cache helpers."""
    return GitHubActionsClient().command(*args, stdin=stdin, timeout=command_timeout)


class GitHubActionsClient:
    def __init__(self, runner: ProcessRunner = run_process, *, sleep=time.sleep,
                 clock=time.monotonic, opener=None):
        self.runner, self.sleep, self.clock = runner, sleep, clock
        self.opener = opener or build_opener(NoRedirect())
        self._token = None

    def command(self, *args, stdin=None, timeout=180) -> str:
        try:
            result = self.runner(['gh', *args], stdin=stdin, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RecoverableError('GitHub CLI transport interrupted') from exc
        if result.returncode:
            match = re.search(r'\bHTTP\s*([1-5]\d\d)\b', result.stderr + result.stdout, re.I)
            status = int(match.group(1)) if match else None
            if status is None or status in {408, 429} or status >= 500:
                raise RecoverableError(f'GitHub read unavailable (HTTP {status or "unknown"})')
            raise GhCommandError(status)
        return result.stdout

    def api(self, endpoint: str) -> dict:
        try:
            data = json.loads(self.command('api', endpoint))
        except json.JSONDecodeError as exc:
            raise RecoverableError('GitHub returned invalid JSON') from exc
        if not isinstance(data, dict):
            raise ExecutionError('Expected a GitHub JSON object')
        return data

    def validate(self, identity: DispatchIdentity) -> None:
        """Read-only checks in the target repository before persisting intent."""
        root = f'repos/{identity.repo}'
        workflow = self.api(f'{root}/actions/workflows/{quote(identity.workflow, safe="")}')
        if workflow.get('state') not in (None, 'active'):
            raise ExecutionError('Workflow is not active')
        for kind in ('heads', 'tags'):
            try:
                self.api(f'{root}/git/ref/{kind}/{quote(identity.ref, safe="")}')
                return
            except GhCommandError as exc:
                if exc.http_status != 404:
                    raise
        raise ExecutionError(f'Ref {identity.ref!r} is neither a branch nor a tag in {identity.repo}')

    def dispatch(self, identity: DispatchIdentity, inputs: dict[str, str]) -> int:
        """Send exactly one POST; response loss is an ambiguous outcome."""
        body = {'ref': identity.ref, 'return_run_details': True}
        if inputs:
            body['inputs'] = inputs
        try:
            raw = self.command('api', '--method', 'POST',
                               f'repos/{identity.repo}/actions/workflows/{quote(identity.workflow, safe="")}/dispatches',
                               '--input', '-', stdin=json.dumps(body))
            value = json.loads(raw).get('workflow_run_id')
            if type(value) is not int or value <= 0:
                raise ValueError('Missing Run ID')
            return value
        except GhCommandError as exc:
            if exc.http_status in REJECTED_STATUSES:
                raise DispatchRejected(exc.http_status) from exc
            raise DispatchUnknown('Dispatch outcome UNKNOWN; inspect GitHub and use --resume RUN_ID') from exc
        except (RecoverableError, ValueError, AttributeError) as exc:
            raise DispatchUnknown('Dispatch outcome UNKNOWN; inspect GitHub and use --resume RUN_ID') from exc

    def get_run(self, identity: DispatchIdentity, run_id: int) -> dict:
        """Verify repository and workflow before associating a supplied Run ID."""
        run = self.api(f'repos/{identity.repo}/actions/runs/{run_id}')
        repo = run.get('repository', {}).get('full_name', identity.repo)
        workflow = run.get('path', '').split('@', 1)[0].rsplit('/', 1)[-1]
        if repo.lower() != identity.repo or (identity.workflow.endswith(('.yml', '.yaml'))
                                           and workflow and workflow != identity.workflow.rsplit('/', 1)[-1]):
            raise ExecutionError('Run does not belong to the requested repository/workflow')
        if identity.workflow.isdigit() and str(run.get('workflow_id')) != identity.workflow:
            raise ExecutionError('Run belongs to a different workflow ID')
        if type(run.get('id')) is int and run['id'] != run_id:
            raise ExecutionError('GitHub returned a different Run ID')
        return run

    def wait(self, identity, run_id, timeout, interval) -> dict:
        deadline = self.clock() + timeout
        previous = None
        while True:
            run = self.get_run(identity, run_id)
            status = run.get('status')
            if status != previous:
                print(f'Run {run_id}: {status} / {run.get("conclusion") or "-"}', flush=True)
                previous = status
            if status == 'completed':
                if not run.get('conclusion'):
                    raise RecoverableError('Remote result is not yet available')
                return run
            if self.clock() >= deadline:
                raise RecoverableError(f'Waiting for Run {run_id} timed out; resume the same Run')
            self.sleep(min(interval, max(0, deadline - self.clock())))

    def artifacts(self, repo: str, run_id: int) -> list[ArtifactInfo]:
        """Read the full paginated artifact list for one Run."""
        result = []
        page = 1
        while True:
            data = self.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100&page={page}')
            batch = data.get('artifacts')
            if not isinstance(batch, list):
                raise ExecutionError('Invalid GitHub artifact listing')
            try:
                result.extend(ArtifactInfo(int(a['id']), a['name'], int(a['size_in_bytes']),
                                           a.get('digest'), bool(a.get('expired'))) for a in batch)
            except (KeyError, TypeError, ValueError) as exc:
                raise ExecutionError('Invalid GitHub artifact metadata') from exc
            if len(batch) < 100:
                return result
            page += 1

    def artifact(self, repo: str, artifact_id: int) -> ArtifactInfo:
        data = self.api(f'repos/{repo}/actions/artifacts/{artifact_id}')
        try:
            return ArtifactInfo(int(data['id']), data['name'], int(data['size_in_bytes']),
                                data.get('digest'), bool(data.get('expired')))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionError('Invalid GitHub artifact metadata') from exc

    def download_address(self, endpoint: str) -> str:
        """Get the official redirect; credentials never reach the download host."""
        if self._token is None:
            self._token = self.command('auth', 'token').strip()
        request = Request('https://api.github.com/' + endpoint, headers={
            'Authorization': 'Bearer ' + self._token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'engineering-ci-ghwf',
        })
        try:
            with self.opener.open(request, timeout=30) as response:
                location = response.headers.get('Location')
        except HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get('Location')
                exc.close()
            else:
                raise RecoverableError(f'GitHub download address unavailable (HTTP {exc.code})') from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise RecoverableError('GitHub download address request interrupted') from exc
        from urllib.parse import urlsplit
        if not location or urlsplit(location).scheme != 'https':
            raise ExecutionError('GitHub did not return a secure temporary download address')
        return location
