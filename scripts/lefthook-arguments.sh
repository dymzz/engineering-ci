#!/bin/sh

_venv_has_eci() {
  if [ -x "$1/Scripts/python.exe" ]; then
    "$1/Scripts/python.exe" -I -c "import engineering_ci" </dev/null >/dev/null 2>&1
  elif [ -x "$1/bin/python" ]; then
    "$1/bin/python" -I -c "import engineering_ci" </dev/null >/dev/null 2>&1
  else
    return 1
  fi
}

_configure_worktree_uv_env() {
  if [ -n "${UV_PROJECT:-}" ]; then
    return 0
  fi
  git_dir=$(git rev-parse --path-format=absolute --git-dir </dev/null 2>/dev/null) || return 0
  common_dir=$(git rev-parse --path-format=absolute --git-common-dir </dev/null 2>/dev/null) || return 0
  if [ -z "$git_dir" ] || [ -z "$common_dir" ] || [ "$git_dir" = "$common_dir" ]; then
    return 0
  fi
  if [ "$(basename "$common_dir")" != ".git" ]; then
    return 0
  fi
  worktree_root=$(git rev-parse --path-format=absolute --show-toplevel </dev/null 2>/dev/null) || return 0
  if [ -z "$worktree_root" ]; then
    return 0
  fi
  main_root=$(dirname "$common_dir")
  main_venv="$main_root/.venv"
  use_shared_venv=0
  if [ -z "${UV_PROJECT_ENVIRONMENT:-}" ]; then
    if ! _venv_has_eci "$worktree_root/.venv" && _venv_has_eci "$main_venv"; then
      UV_PROJECT_ENVIRONMENT="$main_venv"
      export UV_PROJECT_ENVIRONMENT
      use_shared_venv=1
    fi
  elif [ -d "$UV_PROJECT_ENVIRONMENT" ] && [ -d "$main_venv" ]; then
    env_real=$(cd "$UV_PROJECT_ENVIRONMENT" 2>/dev/null && pwd -P)
    main_real=$(cd "$main_venv" 2>/dev/null && pwd -P)
    if [ -n "$env_real" ] && [ "$env_real" = "$main_real" ]; then
      use_shared_venv=1
    fi
  fi
  if [ "$use_shared_venv" = "1" ] && [ -f "$worktree_root/src/engineering_ci/cli/__init__.py" ] && [ -f "$main_root/src/engineering_ci/cli/__init__.py" ]; then
    worktree_src="$worktree_root/src"
    case "$(uname -s 2>/dev/null)" in
      MINGW*|MSYS*|CYGWIN*) path_sep=';' ;;
      *) path_sep=':' ;;
    esac
    case "${PYTHONPATH:-}" in
      "") PYTHONPATH="$worktree_src" ;;
      "$worktree_src"|"$worktree_src${path_sep}"*) ;;
      *) PYTHONPATH="${worktree_src}${path_sep}${PYTHONPATH}" ;;
    esac
    export PYTHONPATH
  fi
}

_configure_worktree_uv_env

case "$2" in
  pre-commit)
    exec uv run --locked --no-sync lefthook run pre-commit --no-stage-fixed
    ;;
  pre-push)
    {
      remote_name=$(printf '%s' "$3" | base64 | tr -d '[:space:]')
      remote_location=$(printf '%s' "$4" | base64 | tr -d '[:space:]')
      printf 'ECI-PRE-PUSH:%s:%s:' "$remote_name" "$remote_location"
      cat
    } | uv run --locked --no-sync lefthook run pre-push
    ;;
  *)
    exec uv run --locked --no-sync lefthook "$@"
    ;;
esac
