#!/bin/sh

_resolve_primary_root() {
  common_dir=$1
  is_bare=$(git --git-dir="$common_dir" config --bool --get core.bare </dev/null 2>/dev/null)
  if [ "$is_bare" = "true" ]; then
    return 1
  fi
  configured_wt=$(git --git-dir="$common_dir" config --path --get core.worktree </dev/null 2>/dev/null)
  if [ -n "$configured_wt" ]; then
    case "$configured_wt" in
      /*|[A-Za-z]:/*|[A-Za-z]:\\*)
        printf '%s\n' "$configured_wt"
        ;;
      *)
        (cd "$common_dir" 2>/dev/null && cd "$configured_wt" 2>/dev/null && pwd -P)
        ;;
    esac
    return 0
  fi
  if [ "$(basename "$common_dir")" = ".git" ]; then
    dirname "$common_dir"
    return 0
  fi
  return 1
}

_prepend_pythonpath() {
  entry=$1
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) path_sep=';' ;;
    *) path_sep=':' ;;
  esac
  case "${PYTHONPATH:-}" in
    "") PYTHONPATH="$entry" ;;
    "$entry"|"$entry${path_sep}"*) ;;
    *) PYTHONPATH="${entry}${path_sep}${PYTHONPATH}" ;;
  esac
  export PYTHONPATH
}

_configure_worktree_uv_env() {
  worktree_root=$(git rev-parse --path-format=absolute --show-toplevel </dev/null 2>/dev/null) || return 0
  if [ -z "$worktree_root" ]; then
    return 0
  fi
  git_dir=$(git rev-parse --path-format=absolute --git-dir </dev/null 2>/dev/null) || return 0
  common_dir=$(git rev-parse --path-format=absolute --git-common-dir </dev/null 2>/dev/null) || return 0

  # 1. Default linked worktrees to the primary checkout's unified .venv unless
  #    the caller explicitly selects UV_PROJECT_ENVIRONMENT or UV_PROJECT.
  if [ -z "${UV_PROJECT:-}" ] && [ -z "${UV_PROJECT_ENVIRONMENT:-}" ] && [ -n "$git_dir" ] && [ -n "$common_dir" ] && [ "$git_dir" != "$common_dir" ]; then
    if primary_root=$(_resolve_primary_root "$common_dir") && [ -n "$primary_root" ]; then
      UV_PROJECT_ENVIRONMENT="$primary_root/.venv"
      export UV_PROJECT_ENVIRONMENT
    fi
  fi

  # 2. Verify runtime environment readiness natively via read-only
  #    `uv sync --locked --check --no-install-project --no-dev --inexact --offline`
  #    against the active uv project (explicit UV_PROJECT or current worktree)
  #    without gating Hook runs on inapplicable full dev tools (such as ruff/pytest).
  #    Lefthook itself lives in [dependency-groups] dev and is ensured when preparing
  #    the environment via --group dev (uv run --no-sync falls back to PATH if absent).
  project_root="${UV_PROJECT:-$worktree_root}"
  if [ "$project_root" != "/" ]; then
    project_root="${project_root%/}"
  fi
  if [ -f "$project_root/pyproject.toml" ] && [ -f "$project_root/uv.lock" ]; then
    check_output=$(uv sync --locked --check --no-install-project --no-dev --inexact --offline </dev/null 2>&1)
    check_rc=$?
    if [ "$check_rc" -ne 0 ]; then
      if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
        target_env="$UV_PROJECT_ENVIRONMENT"
      else
        target_env="$project_root/.venv"
      fi
      printf '%s\n' "$check_output" >&2
      printf 'engineering-ci hook: uv runtime environment is missing or does not satisfy %s/uv.lock: %s\n' "$project_root" "$target_env" >&2
      printf 'Note: runtime readiness uses --no-dev; Lefthook (lefthook==2.2.2) comes from --group dev in the prepared environment (inspect via: uv run --locked --no-sync lefthook version).\n' >&2
      printf 'Prepare or update the shared environment from its managing checkout (preserving compatible superset packages):\n  uv sync --locked --inexact --group dev\n' >&2
      printf 'To update the shared environment for compatible locked dependencies from another worktree without changing its editable binding:\n  UV_PROJECT_ENVIRONMENT=%s uv sync --locked --no-install-project --inexact --group dev\n' "$target_env" >&2
      printf 'For a branch with incompatible locked dependencies, prepare and select a dedicated environment:\n  UV_PROJECT_ENVIRONMENT=<path> uv sync --locked --group dev\n' >&2
      exit "$check_rc"
    fi
  fi

  # 3. Independently bind candidate ECI source whenever executing inside an ECI
  #    checkout, regardless of whether the shared env is default or overridden.
  if [ -f "$worktree_root/src/engineering_ci/cli/__init__.py" ]; then
    _prepend_pythonpath "$worktree_root/src"
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
