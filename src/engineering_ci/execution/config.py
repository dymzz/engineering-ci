"""Optional ghwf download settings, anchored to engineering-ci rather than cwd."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from .download import DownloadConfig, DownloadConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OPTIONS = {
    'backend': ('download_backend', 'GHWF_DOWNLOAD_BACKEND', str),
    'aria2c_path': ('aria2c', 'GHWF_ARIA2C_PATH', str),
    'connections': ('connections', 'GHWF_DOWNLOAD_CONNECTIONS', int),
    'timeout': ('download_timeout', 'GHWF_DOWNLOAD_TIMEOUT', int),
    'connect_timeout': ('connect_timeout', 'GHWF_CONNECT_TIMEOUT', int),
    'process_timeout': ('download_process_timeout', 'GHWF_DOWNLOAD_PROCESS_TIMEOUT', int),
    'recovery': ('download_recovery', 'GHWF_DOWNLOAD_RECOVERY', str),
}


def load_download_config(args=None, *, environ=None, project_root=None) -> DownloadConfig:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    path = root / 'config' / 'ghwf.local.toml'
    local = {}
    if path.exists():
        try:
            import tomllib
            with path.open('rb') as stream:
                local = tomllib.load(stream).get('download', {})
        except (OSError, ValueError, ImportError) as exc:
            raise DownloadConfigurationError('Cannot read config/ghwf.local.toml; valid TOML and Python 3.11+ required') from exc
        if not isinstance(local, dict) or local.keys() - _OPTIONS.keys():
            raise DownloadConfigurationError('Unknown or invalid [download] settings in config/ghwf.local.toml')
    env = os.environ if environ is None else environ
    defaults = DownloadConfig()
    values = {}
    for name, (cli_name, env_name, convert) in _OPTIONS.items():
        default = defaults.executable if name == 'aria2c_path' else getattr(defaults, name)
        value = getattr(args, cli_name, None) if args is not None else None
        if value is None:
            value = env.get(env_name, local.get(name, default))
        if name == 'aria2c_path' and value == '':
            value = default
        try:
            if convert is int:
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError
                values[name] = int(value)
            elif not isinstance(value, str):
                raise ValueError
            else:
                values[name] = value
        except (ValueError, TypeError):
            raise DownloadConfigurationError(f'Invalid download setting: {name} ({env_name})') from None
    executable = values.pop('aria2c_path')
    # Keep a Windows absolute path intact on Linux: auto can fall back and aria2
    # can report a missing tool. Never turn it into a cwd-relative Linux path.
    if ('/' in executable or '\\' in executable) and not (
            PurePosixPath(executable).is_absolute() or PureWindowsPath(executable).is_absolute()):
        executable = str(root / executable)
    try:
        return DownloadConfig(executable=executable, **values)
    except ValueError as exc:
        raise DownloadConfigurationError(str(exc)) from None
