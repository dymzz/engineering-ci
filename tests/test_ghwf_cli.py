"""Legacy command parameters, origin resolution and v1 cache compatibility."""
import json
from pathlib import Path

import pytest

from engineering_ci.execution import cli, repository
from engineering_ci.execution.models import CollectionIdentity, DispatchIdentity
from engineering_ci.execution.store import StateStore, atomic_json


def test_entrypoint_and_all_old_parameters():
    args = cli.parser().parse_args(['--repo', 'o/r', '--workflow', 'ci.yml', '--ref', 'topic/a',
        '--input', 'key=value', '--artifact', 'build-{run_id}', '--out', 'outputs', '--save-log',
        '--resume', '123', '--show-state', '--refresh-repo', '--interval', '2', '--timeout', '30',
        '--retry-attempts', '4', '--retry-delay', '2'])
    assert args.resume == '123' and args.save_log
    assert cli.parser().parse_args(['--workflow', 'ci.yml', '--resume', '--no-artifacts']).resume == 'last'
    assert cli.parser().parse_args(['--workflow', 'ci.yml', '--new-run']).new_run


@pytest.mark.parametrize('origin', ['git@github.com:owner/repo.git', 'https://github.com/owner/repo.git',
                                   'ssh://git@github.com/owner/repo.git'])
def test_origin_parsing(origin):
    assert repository.repo_from_origin(origin) == 'owner/repo'


def test_existing_repo_cache_rules(tmp_path, monkeypatch):
    origin = ['git@github.com:owner/one.git']
    def git(*args):
        return str(tmp_path) if args[0] == 'rev-parse' else origin[0]
    monkeypatch.setattr(repository, 'git_value', git)
    cache = tmp_path / 'cache.json'
    assert repository.resolve_repo(cache_path=cache) == ('owner/one', 'git origin')
    assert repository.resolve_repo(cache_path=cache) == ('owner/one', 'cache')
    origin[0] = 'git@github.com:owner/two.git'
    assert repository.resolve_repo(cache_path=cache) == ('owner/two', 'git origin')
    assert repository.resolve_repo(refresh=True, cache_path=cache)[1] == 'git origin'


def test_legacy_show_state_offline_and_inputs_not_saved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'default_cache_dir', lambda: tmp_path)
    out = tmp_path / 'out'
    old = cli.legacy_state_file('owner/repo', 'ci.yml', 'main', {'secret': 'TOKEN_VALUE'},
                                 out, None, True)
    atomic_json(old, {'version': 1, 'stage': 'running', 'run_id': 123})
    assert cli.main(['--repo', 'owner/repo', '--workflow', 'ci.yml', '--out', str(out),
                     '--save-log', '--input', 'secret=TOKEN_VALUE', '--show-state']) == 0
    text = capsys.readouterr().out
    assert 'TOKEN_VALUE' not in text and '"run_id": 123' in text
    identity = DispatchIdentity.create('owner/repo', 'ci.yml', 'main', {'secret': 'TOKEN_VALUE'})
    assert StateStore(tmp_path / 'runs' / f'{identity.key}.json').load().run_id == 123


def test_ambiguous_legacy_scope_blocks_blind_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'default_cache_dir', lambda: tmp_path)
    atomic_json(tmp_path / 'runs/unknown-scope.json', {'version': 1, 'stage': 'running',
        'repo': 'owner/repo', 'workflow': 'ci.yml', 'ref': 'main', 'run_id': 123})
    with pytest.raises(RuntimeError, match='identity is incomplete'):
        cli.main(['--repo', 'owner/repo', '--workflow', 'ci.yml', '--out', str(tmp_path / 'different')])
