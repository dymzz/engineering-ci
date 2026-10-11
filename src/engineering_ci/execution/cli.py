"""ghwf application arguments and compatibility cache lookup."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from ..cli._execution import add_runtime_arguments
from .collector import ArtifactCollector
from .coordinator import ExecutionCoordinator
from .github import GitHubActionsClient
from .repository import REPO_PATTERN, default_cache_dir, resolve_repo
from .archive import ArchiveLimits
from .download import DownloadAdapter
from .config import load_download_config
from .models import CollectionIdentity, DispatchIdentity, ExecutionError
from .store import StateStore


def legacy_state_file(repo, workflow, ref, inputs, out, artifact, save_log, no_artifacts=False):
    """Reproduce the v1 scope hash to find existing checkpoints."""
    scope = json.dumps([repo, workflow, ref, inputs, str(out.resolve()),
                        artifact, save_log, no_artifacts], sort_keys=True)
    return default_cache_dir() / 'runs' / f'{hashlib.sha256(scope.encode()).hexdigest()[:24]}.json'


class _RunOnlyCollector:
    """A request/wait operation has no required local files, even for a failed Run."""

    def intact(self, record) -> bool:
        return (record.collection.no_artifacts and not record.collection.save_log
                and record.collection.artifact is None)

    def collect(self, record, checkpoint) -> None:
        raise ExecutionError('Workflow-only execution cannot collect local files')


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Dispatch/resume a GitHub Actions run and collect its outputs.')
    p.add_argument('--repo', help='OWNER/REPO; infer from current Git origin when omitted')
    p.add_argument('--workflow', required=True)
    p.add_argument('--ref', default='main')
    p.add_argument('--input', action='append', default=[], metavar='KEY=VALUE')
    add_runtime_arguments(p)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--resume', nargs='?', const='last', metavar='RUN_ID')
    group.add_argument('--new-run', action='store_true')
    return p


def main(argv=None, *, collect_outputs=True) -> int:
    p = parser()
    args = p.parse_args(argv)
    if args.repo and not REPO_PATTERN.fullmatch(args.repo):
        p.error('--repo must be OWNER/REPO')
    if not args.workflow or not args.ref:
        p.error('--workflow and --ref cannot be empty')
    if args.interval <= 0 or args.timeout <= 0 or args.retry_attempts < 1 or args.retry_delay < 0:
        p.error('interval, timeout and retry attempts must be positive; delay cannot be negative')
    if args.no_artifacts and args.artifact:
        p.error('--no-artifacts and --artifact cannot be combined')
    if collect_outputs:
        try:
            config = load_download_config(args)
            if min(args.max_extract_bytes, args.max_extract_files) <= 0:
                raise ValueError('Extraction limits must be positive')
        except ValueError as exc:
            p.error(str(exc))
    if args.resume not in (None, 'last') and (not args.resume.isdigit() or int(args.resume) <= 0):
        p.error('--resume requires a positive Run ID')
    inputs = {}
    for item in args.input:
        if '=' not in item or not item.split('=', 1)[0]:
            p.error('--input must be KEY=VALUE with a nonempty key')
        key, value = item.split('=', 1)
        inputs[key] = value
    repo, source = (args.repo, '--repo') if args.repo else resolve_repo(refresh=args.refresh_repo)
    print(f'Repository: {repo} ({source}); workflow: {args.workflow}; ref: {args.ref}', flush=True)
    identity = DispatchIdentity.create(repo, args.workflow, args.ref, inputs)
    collection = (CollectionIdentity.create(Path(args.out), args.artifact, args.save_log, args.no_artifacts)
                  if collect_outputs else CollectionIdentity.create(default_cache_dir(), no_artifacts=True))
    store = StateStore(default_cache_dir() / 'runs' / f'{identity.key}.json')
    old_path = legacy_state_file(repo, args.workflow, args.ref, inputs, Path(collection.out),
                                 collection.artifact, collection.save_log, collection.no_artifacts)
    # v1 did not persist inputs: only exact scopes are safe when inputs are supplied.
    legacy_paths = [old_path]
    # v1 omitted inputs and collection paths. A different scope cannot be inferred
    # safely; matching unfinished legacy metadata blocks a blind new dispatch.
    if not args.new_run and args.resume in (None, 'last') and not store.path.exists() and not old_path.exists():
        for candidate in old_path.parent.glob('*.json'):
            try:
                old = json.loads(candidate.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                raise ExecutionError(f'Unreadable legacy candidate {candidate}; use --resume RUN_ID')
            if (old.get('version') == 1 and str(old.get('repo', '')).lower() == identity.repo
                    and old.get('workflow') == identity.workflow and old.get('ref') == identity.ref
                    and old.get('stage') not in ('dispatch_rejected', 'finished')):
                raise ExecutionError(f'Legacy task identity is incomplete; use --resume {old.get("run_id") or "RUN_ID"}')
    if args.show_state:
        with store.lock():
            state = store.load() or store.migrate(legacy_paths, identity, collection)
        print(f'State file: {store.path}')
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False) if state else 'No saved state')
        return 0
    if not shutil.which('gh'):
        p.error('GitHub CLI (gh) not found; install it and run gh auth login')
    client = GitHubActionsClient()
    collector = (ArtifactCollector(client, DownloadAdapter(config),
                                   ArchiveLimits(args.max_extract_bytes, args.max_extract_files))
                 if collect_outputs else _RunOnlyCollector())
    coordinator = ExecutionCoordinator(store, client, collector,
                                       identity, collection, inputs, timeout=args.timeout,
                                       interval=args.interval)
    result = coordinator.execute(resume=args.resume, new_run=args.new_run, legacy_paths=legacy_paths)
    print(f'Finished run {result.run_id} ({result.remote_conclusion}); state: {store.path}')
    print(f'Run URL: https://github.com/{repo}/actions/runs/{result.run_id}', flush=True)
    return result.exit_code


def entrypoint(argv=None, *, collect_outputs=True) -> int:
    try:
        return main(argv, collect_outputs=collect_outputs)
    except (ExecutionError, OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted; inspect --show-state and resume the saved Run. '
              'UNKNOWN dispatch requires verifying GitHub and --resume RUN_ID.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(entrypoint())
