"""Precedence and platform paths do not depend on the calling repository."""
import shutil
from pathlib import Path

import pytest

from engineering_ci.execution.cli import parser
from engineering_ci.execution.config import load_download_config
from engineering_ci.execution.download import DownloadConfig, DownloadConfigurationError


def write_config(root, text):
    path = root / 'config' / 'ghwf.local.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_absent_config_has_optional_defaults(tmp_path):
    assert load_download_config(environ={}, project_root=tmp_path) == DownloadConfig()


def test_all_settings_precedence(tmp_path):
    write_config(tmp_path, '[download]\nbackend="native"\naria2c_path="tools/aria2c"\n'
                 'connections=2\ntimeout=3\nconnect_timeout=4\nprocess_timeout=5\nrecovery="pause"\n')
    local = load_download_config(environ={}, project_root=tmp_path)
    assert local == DownloadConfig(2, 3, 4, 5, 'pause', str(tmp_path / 'tools/aria2c'), 'native')
    env = {'GHWF_DOWNLOAD_BACKEND': 'aria2', 'GHWF_ARIA2C_PATH': 'env/aria2c',
           'GHWF_DOWNLOAD_CONNECTIONS': '6', 'GHWF_DOWNLOAD_TIMEOUT': '7',
           'GHWF_CONNECT_TIMEOUT': '8', 'GHWF_DOWNLOAD_PROCESS_TIMEOUT': '9',
           'GHWF_DOWNLOAD_RECOVERY': 'restart'}
    assert load_download_config(environ=env, project_root=tmp_path) == DownloadConfig(
        6, 7, 8, 9, 'restart', str(tmp_path / 'env/aria2c'), 'aria2')
    args = parser().parse_args(['--workflow', 'ci.yml', '--download-backend', 'auto', '--aria2c', 'cli/aria2c',
                               '--connections', '10', '--download-timeout', '11', '--connect-timeout', '12',
                               '--download-process-timeout', '13', '--download-recovery', 'pause'])
    assert load_download_config(args, environ=env, project_root=tmp_path) == DownloadConfig(
        10, 11, 12, 13, 'pause', str(tmp_path / 'cli/aria2c'), 'auto')


def test_root_is_independent_of_consumer_cwd(tmp_path, monkeypatch):
    root, consumer = tmp_path / 'engineering-ci', tmp_path / 'consumer'
    consumer.mkdir()
    write_config(root, '[download]\nbackend="native"\naria2c_path=".test-tools/aria2c"\n')
    write_config(consumer, '[download]\nbackend="aria2"\n')
    import engineering_ci.execution.config as settings
    monkeypatch.setattr(settings, 'PROJECT_ROOT', root)
    monkeypatch.chdir(consumer)
    result = load_download_config(environ={})
    assert result.backend == 'native' and result.executable == str(root / '.test-tools/aria2c')


@pytest.mark.parametrize('executable', [
    'D:/tools/aria2/aria2c.exe', r'D:\tools with spaces\aria2c.exe',
    '/usr/bin/aria2c', '/opt/tools with spaces/aria2c', 'aria2c',
])
def test_windows_linux_env_paths_kept_intact(tmp_path, executable):
    config = load_download_config(environ={'GHWF_ARIA2C_PATH': executable}, project_root=tmp_path)
    assert config.executable == executable


@pytest.mark.parametrize('executable', ['D:/tools/aria2c.exe', '/opt/tools/aria2c'])
def test_platform_path_passed_to_lookup(tmp_path, monkeypatch, executable):
    from engineering_ci.execution.download import DownloadAdapter
    from engineering_ci.execution.transport import ResourceIdentity
    seen = []
    def which(value):
        seen.append(value)
        return None
    monkeypatch.setattr(shutil, 'which', which)
    config = load_download_config(environ={'GHWF_ARIA2C_PATH': executable,
                                          'GHWF_DOWNLOAD_BACKEND': 'aria2'}, project_root=tmp_path)
    with pytest.raises(DownloadConfigurationError):
        DownloadAdapter(config).download(lambda: pytest.fail('No URL'), ResourceIdentity('x'), tmp_path / 'x')
    assert seen == [executable]


@pytest.mark.parametrize('text', ['[download', '[download]\nbackend="oops"',
                                  '[download]\nconnections=true', '[download]\ntimeout=1.5',
                                  '[download]\nunknown=1', 'download="native"'])
def test_bad_local_config_is_configuration_error(tmp_path, text):
    write_config(tmp_path, text)
    with pytest.raises(DownloadConfigurationError):
        load_download_config(environ={}, project_root=tmp_path)


@pytest.mark.parametrize('name,value', [('GHWF_DOWNLOAD_BACKEND', 'oops'), ('GHWF_DOWNLOAD_CONNECTIONS', '0'),
                                     ('GHWF_DOWNLOAD_TIMEOUT', 'abc'), ('GHWF_DOWNLOAD_RECOVERY', 'retry')])
def test_bad_env_is_configuration_error(tmp_path, name, value):
    with pytest.raises(DownloadConfigurationError):
        load_download_config(environ={name: value}, project_root=tmp_path)


def test_cli_can_override_invalid_lower_priority_value(tmp_path):
    write_config(tmp_path, '[download]\nbackend="oops"\nconnections=0\n')
    args = parser().parse_args(['--workflow', 'ci.yml', '--download-backend', 'native', '--connections', '2'])
    assert load_download_config(args, environ={'GHWF_DOWNLOAD_BACKEND': 'bad'}, project_root=tmp_path).backend == 'native'


def test_empty_path_searches_path(tmp_path):
    write_config(tmp_path, '[download]\naria2c_path=""\n')
    assert load_download_config(environ={}, project_root=tmp_path).executable == 'aria2c'
