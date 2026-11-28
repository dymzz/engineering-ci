#!/bin/sh

# Shared host environment binding and readiness contract for the engineering-ci
# local entry and Git Hook adapter. This file is sourced; it performs no
# top-level work. It resolves the actual checkout/project/environment, validates
# the selected interpreter, candidate source and required native tools, and
# performs one read-only locked sync check per operation scope. It never creates
# or synchronizes an environment.

_eci_is_windows() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

_eci_path_sep() {
  if _eci_is_windows; then
    printf ';'
  else
    printf ':'
  fi
}

_eci_native_path() {
  if _eci_is_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s\n' "$1"
  fi
}

_eci_unix_path() {
  if _eci_is_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$1"
  else
    printf '%s\n' "$1"
  fi
}

_eci_abspath() {
  _eci_input=$1
  case "$_eci_input" in
    /*|[A-Za-z]:/*|[A-Za-z]:\\*) ;;
    *) _eci_input="$PWD/$_eci_input" ;;
  esac
  if [ -d "$_eci_input" ]; then
    (cd "$_eci_input" 2>/dev/null && pwd -P)
    return $?
  fi
  _eci_dir=$(dirname "$_eci_input")
  _eci_base=$(basename "$_eci_input")
  if _eci_resolved=$(cd "$_eci_dir" 2>/dev/null && pwd -P); then
    printf '%s/%s\n' "$_eci_resolved" "$_eci_base"
  else
    printf '%s\n' "$_eci_input"
  fi
}

_eci_error() {
  printf 'engineering-ci entry: %s\n' "$*" >&2
}

_eci_warn() {
  printf 'engineering-ci entry: %s\n' "$*" >&2
}

# -- Existing Hook binding helpers (#170 behavior preserved) -----------------

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
  path_sep=$(_eci_path_sep)
  case "${PYTHONPATH:-}" in
    "") PYTHONPATH="$entry" ;;
    "$entry"|"$entry${path_sep}"*) ;;
    *) PYTHONPATH="${entry}${path_sep}${PYTHONPATH}" ;;
  esac
  export PYTHONPATH
}

# The Hook adapter continues to use the #170 bootstrap semantics, including
# inherited UV_PROJECT / UV_PROJECT_ENVIRONMENT compatibility and a runtime-only
# readiness check before Lefthook runs.
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

# -- Normal local entry binding ---------------------------------------------

eci_entry_resolve() {
  eci_mode=${1:-normal}
  eci_explicit_env=${2:-}
  _eci_worktree=$(git rev-parse --path-format=absolute --show-toplevel </dev/null 2>/dev/null) || {
    _eci_error "not inside a Git checkout"
    return 2
  }
  _eci_git_dir=$(git rev-parse --path-format=absolute --git-dir </dev/null 2>/dev/null) || {
    _eci_error "cannot resolve the Git directory"
    return 2
  }
  _eci_common_dir=$(git rev-parse --path-format=absolute --git-common-dir </dev/null 2>/dev/null) || {
    _eci_error "cannot resolve the Git common directory"
    return 2
  }
  ECI_WORKTREE_ROOT=$_eci_worktree
  ECI_SRC_DIR="$ECI_WORKTREE_ROOT/src"
  if [ ! -f "$ECI_SRC_DIR/engineering_ci/cli/__init__.py" ]; then
    _eci_error "not an engineering-ci checkout: $ECI_WORKTREE_ROOT"
    return 2
  fi
  ECI_PROJECT_ROOT=$ECI_WORKTREE_ROOT
  ECI_PRIMARY_ROOT=""
  ECI_ENV_DIR=""
  ECI_PYTHON=""

  if [ "$eci_mode" = hook ] && [ -n "${UV_PROJECT:-}" ]; then
    ECI_PROJECT_ROOT=$(_eci_abspath "$UV_PROJECT")
  fi

  if [ -n "$eci_explicit_env" ]; then
    ECI_ENV_DIR=$(_eci_abspath "$eci_explicit_env")
  elif [ "$eci_mode" = hook ] && [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
    ECI_ENV_DIR=$(_eci_abspath "$UV_PROJECT_ENVIRONMENT")
  elif [ "$eci_mode" = hook ] && [ -n "${UV_PROJECT:-}" ]; then
    ECI_ENV_DIR=""
  elif [ "$_eci_git_dir" != "$_eci_common_dir" ]; then
    _eci_primary=$(_resolve_primary_root "$_eci_common_dir") || {
      _eci_error "cannot resolve the primary checkout for linked worktree $ECI_WORKTREE_ROOT"
      return 2
    }
    if [ -z "$_eci_primary" ]; then
      _eci_error "cannot resolve the primary checkout for linked worktree $ECI_WORKTREE_ROOT"
      return 2
    fi
    ECI_PRIMARY_ROOT=$_eci_primary
    ECI_ENV_DIR="$_eci_primary/.venv"
  else
    ECI_PRIMARY_ROOT=$ECI_WORKTREE_ROOT
    ECI_ENV_DIR="$ECI_WORKTREE_ROOT/.venv"
  fi

  if [ -z "$ECI_PRIMARY_ROOT" ]; then
    if [ -n "$ECI_ENV_DIR" ] && [ "$_eci_git_dir" != "$_eci_common_dir" ]; then
      ECI_PRIMARY_ROOT=$(_resolve_primary_root "$_eci_common_dir" 2>/dev/null || true)
    else
      ECI_PRIMARY_ROOT=$ECI_WORKTREE_ROOT
    fi
  fi
  return 0
}

eci_entry_python() {
  for candidate in "$ECI_ENV_DIR/Scripts/python.exe" "$ECI_ENV_DIR/bin/python"; do
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

eci_entry_bin_dir() {
  if [ -d "$ECI_ENV_DIR/Scripts" ]; then
    printf '%s\n' "$ECI_ENV_DIR/Scripts"
  else
    printf '%s\n' "$ECI_ENV_DIR/bin"
  fi
}

_eci_lock_version() {
  _eci_lock=$1
  _eci_pkg=$2
  [ -f "$_eci_lock" ] || return 1
  awk -v pkg="$_eci_pkg" '
    /^\[\[package\]\]/ { in_pkg=1; name=""; version=""; next }
    in_pkg && /^name = "/ { line=$0; sub(/^name = "/, "", line); sub(/"$/, "", line); name=line; next }
    in_pkg && /^version = "/ { line=$0; sub(/^version = "/, "", line); sub(/"$/, "", line); version=line; if (name == pkg) { print version; exit } next }
  ' "$_eci_lock"
}

_eci_require_metadata_distribution() {
  _eci_dist_name=$1
  _eci_lock_name=$2
  _eci_dist_result=$(PYTHONPATH='' "$ECI_PYTHON" -P -c '
import importlib.metadata as metadata
import pathlib
import sys

distribution = next(
    (item for item in metadata.distributions() if item.metadata.get("Name") == sys.argv[1]),
    None,
)
if distribution is None:
    print(f"ERROR No package metadata was found for {sys.argv[1]}")
    raise SystemExit(2)
root = pathlib.Path(sys.argv[2]).resolve()
location = distribution.locate_file("").resolve()
if not (location == root or location.is_relative_to(root)):
    print(f"ERROR {sys.argv[1]} is installed outside {root}: {location}")
    raise SystemExit(3)
print(distribution.version)
' "$_eci_dist_name" "$(_eci_native_path "$ECI_ENV_DIR")" </dev/null 2>&1) || {
    _eci_error "$_eci_dist_name is not installed from $ECI_ENV_DIR: $_eci_dist_result"
    return 1
  }
  _eci_expected_version=$(_eci_lock_version "$ECI_PROJECT_ROOT/uv.lock" "$_eci_lock_name")
  if [ -z "$_eci_expected_version" ]; then
    _eci_error "cannot read the locked $_eci_lock_name version from $ECI_PROJECT_ROOT/uv.lock"
    return 1
  fi
  if [ "$_eci_dist_result" != "$_eci_expected_version" ]; then
    _eci_error "$_eci_dist_name distribution version mismatch: environment has $_eci_dist_result, uv.lock requires $_eci_expected_version"
    return 1
  fi
  return 0
}

_eci_require_distribution_tool() {
  _eci_dist_name=$1
  _eci_lock_name=$2
  _eci_bin_name=$3
  _eci_version_cmd=$4
  _eci_bin=$(eci_entry_bin_dir)
  _eci_tool=""
  for _eci_candidate in "$_eci_bin/$_eci_bin_name.exe" "$_eci_bin/$_eci_bin_name"; do
    if [ -f "$_eci_candidate" ]; then
      _eci_tool=$_eci_candidate
      break
    fi
  done
  if [ -z "$_eci_tool" ]; then
    _eci_error "$_eci_bin_name is not available in $_eci_bin; the selected environment is not ready"
    return 1
  fi
  _eci_require_metadata_distribution "$_eci_dist_name" "$_eci_lock_name" || return 1
  _eci_version_output=$(PYTHONPATH='' "$_eci_tool" "$_eci_version_cmd" </dev/null 2>&1) || {
    _eci_error "$_eci_bin_name version check failed for $_eci_tool"
    return 1
  }
  _eci_actual_version=$(printf '%s\n' "$_eci_version_output" | awk 'NF { value=$NF } END { print value }')
  if [ "$_eci_actual_version" != "$_eci_expected_version" ]; then
    _eci_error "$_eci_bin_name version mismatch: $_eci_tool reports $_eci_actual_version, uv.lock requires $_eci_expected_version"
    return 1
  fi
  ECI_TOOL_BIN=$_eci_tool
  return 0
}

_eci_require_pytest() {
  _eci_report_mode=${1:-report}
  # shellcheck disable=SC2034
  _eci_require_distribution_tool pytest pytest pytest --version && ECI_PYTEST_BIN=$ECI_TOOL_BIN || return 1
  if [ "$_eci_report_mode" = report ]; then
    _eci_require_metadata_distribution pytest-reportlog pytest-reportlog || return 1
  fi
  return 0
}

_eci_require_ruff() {
  # shellcheck disable=SC2034
  _eci_require_distribution_tool ruff ruff ruff --version && ECI_RUFF_BIN=$ECI_TOOL_BIN
}

_eci_require_lefthook() {
  _eci_require_distribution_tool lefthook lefthook lefthook version
}

_eci_validate_environment() {
  if [ -z "$ECI_ENV_DIR" ]; then
    _eci_error "no environment selected for $ECI_WORKTREE_ROOT"
    return 1
  fi
  if [ ! -d "$ECI_ENV_DIR" ]; then
    _eci_error "environment is missing: $ECI_ENV_DIR"
    _eci_maintenance_hint
    return 1
  fi
  if ! ECI_PYTHON=$(eci_entry_python); then
    _eci_error "no Python interpreter found in $ECI_ENV_DIR"
    _eci_maintenance_hint
    return 1
  fi
  _eci_prefix=$("$ECI_PYTHON" -P -c 'import sys; print(sys.prefix)' </dev/null 2>/dev/null) || {
    _eci_error "cannot start the selected interpreter: $ECI_PYTHON"
    _eci_maintenance_hint
    return 1
  }
  if _eci_is_windows; then
    _eci_prefix=$(_eci_unix_path "$_eci_prefix")
    _eci_env_cmp=$(cd "$ECI_ENV_DIR" && pwd -P)
    _eci_prefix_cmp=$(printf '%s' "$_eci_prefix" | tr '[:upper:]' '[:lower:]')
    _eci_env_cmp=$(printf '%s' "$_eci_env_cmp" | tr '[:upper:]' '[:lower:]')
  else
    _eci_prefix_cmp=$_eci_prefix
    _eci_env_cmp=$(cd "$ECI_ENV_DIR" && pwd -P)
  fi
  if [ "$_eci_prefix_cmp" != "$_eci_env_cmp" ]; then
    _eci_error "selected interpreter uses sys.prefix $_eci_prefix, not $ECI_ENV_DIR"
    _eci_maintenance_hint
    return 1
  fi

  _eci_expected_python=""
  if [ -f "$ECI_PROJECT_ROOT/.python-version" ]; then
    _eci_expected_python=$(head -n 1 "$ECI_PROJECT_ROOT/.python-version" | tr -d '\r' | awk -F. 'NF >= 2 { print $1"."$2 }')
  elif [ -f "$ECI_PROJECT_ROOT/pyproject.toml" ]; then
    _eci_expected_python=$(grep -E '^requires-python' "$ECI_PROJECT_ROOT/pyproject.toml" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -n 1)
  fi
  if [ -n "$_eci_expected_python" ]; then
    _eci_actual_python=$("$ECI_PYTHON" -P -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    if [ "$_eci_actual_python" != "$_eci_expected_python" ]; then
      _eci_error "interpreter version mismatch: environment has $_eci_actual_python, project requires $_eci_expected_python"
      _eci_maintenance_hint
      return 1
    fi
  fi

  _eci_native_src=$(_eci_native_path "$ECI_SRC_DIR")
  _eci_native_pkg=$(_eci_native_path "$ECI_SRC_DIR/engineering_ci")
  _eci_import_result=$(PYTHONPATH="$_eci_native_src" "$ECI_PYTHON" -P -c '
import pathlib
import sys
import engineering_ci
expected = pathlib.Path(sys.argv[1]).resolve()
actual = pathlib.Path(engineering_ci.__file__).resolve()
print("OK" if actual == expected / "__init__.py" else f"MISMATCH {actual}")
' "$_eci_native_pkg" </dev/null 2>&1) || {
    _eci_error "candidate source binding failed: $ECI_SRC_DIR"
    printf '%s\n' "$_eci_import_result" >&2
    return 1
  }
  if [ "$_eci_import_result" != "OK" ]; then
    _eci_error "candidate source binding points elsewhere: $_eci_import_result"
    return 1
  fi
  return 0
}

_eci_require_persistent_cache() {
  _eci_native_project=$(_eci_native_path "$ECI_PROJECT_ROOT")
  # shellcheck disable=SC2030
  _eci_cache_first=$(
    (
      unset UV_CACHE_DIR UV_NO_CACHE UV_CONFIG_FILE UV_NO_CONFIG
      uv cache dir --project "$_eci_native_project" </dev/null
    ) 2>&1
  ) || {
    _eci_error "cannot resolve the effective uv cache for $ECI_PROJECT_ROOT: $_eci_cache_first"
    _eci_maintenance_hint
    return 1
  }
  _eci_cache_second=$(
    (
      unset UV_CACHE_DIR UV_NO_CACHE UV_CONFIG_FILE UV_NO_CONFIG
      uv cache dir --project "$_eci_native_project" </dev/null
    ) 2>&1
  ) || {
    _eci_error "cannot recheck the effective uv cache for $ECI_PROJECT_ROOT: $_eci_cache_second"
    _eci_maintenance_hint
    return 1
  }
  if [ "$_eci_cache_first" != "$_eci_cache_second" ]; then
    _eci_error "effective uv configuration disables or randomizes the cache (no stable cache directory); the normal entry requires a persistent host cache"
    _eci_error "first cache: $_eci_cache_first"
    _eci_error "second cache: $_eci_cache_second"
    _eci_maintenance_hint
    return 1
  fi
  return 0
}

_eci_sync_check() {
  _eci_scope=$1
  if [ ! -f "$ECI_PROJECT_ROOT/pyproject.toml" ] || [ ! -f "$ECI_PROJECT_ROOT/uv.lock" ]; then
    _eci_error "project metadata is missing under $ECI_PROJECT_ROOT"
    return 1
  fi
  if ! command -v uv >/dev/null 2>&1; then
    _eci_error "uv is not available on PATH"
    return 1
  fi
  case "$_eci_scope" in
    verify) _eci_group_args="--group dev" ;;
    *) _eci_group_args="--no-dev" ;;
  esac
  _eci_native_env=$(_eci_native_path "$ECI_ENV_DIR")
  _eci_native_project=$(_eci_native_path "$ECI_PROJECT_ROOT")
  _eci_native_python=$(_eci_native_path "$ECI_PYTHON")
  # shellcheck disable=SC2030
  # The group choice is one of two fixed flags; intentional word splitting.
  # shellcheck disable=SC2086
  _eci_check_output=$(
    (
      unset UV_CACHE_DIR UV_NO_CACHE UV_CONFIG_FILE UV_NO_CONFIG
      unset UV_NO_DEV UV_NO_GROUP UV_PYTHON UV_PYTHON_PREFERENCE UV_NO_SYNC
      UV_PROJECT_ENVIRONMENT="$_eci_native_env" UV_PROJECT="$_eci_native_project" \
        UV_PYTHON_DOWNLOADS=never \
        uv sync --project "$_eci_native_project" --python "$_eci_native_python" --no-python-downloads --locked --check --no-install-project --inexact --offline $_eci_group_args </dev/null
    ) 2>&1
  )
  _eci_check_rc=$?
  if [ "$_eci_check_rc" -ne 0 ]; then
    printf '%s\n' "$_eci_check_output" >&2
    _eci_error "locked environment readiness failed for $ECI_ENV_DIR (project: $ECI_PROJECT_ROOT)"
    _eci_maintenance_hint
    return "$_eci_check_rc"
  fi
  return 0
}

_eci_maintenance_hint() {
  _eci_hint_env=${ECI_ENV_DIR:-<environment>}
  printf 'Target environment: %s\n' "$_eci_hint_env" >&2
  printf 'Project: %s\n' "${ECI_PROJECT_ROOT:-$ECI_WORKTREE_ROOT}" >&2
  printf 'Prepare/update the shared environment from its managing checkout:\n  uv sync --locked --inexact --group dev\n' >&2
  printf 'Update compatible locked dependencies from another worktree without changing its editable binding:\n  UV_PROJECT_ENVIRONMENT=%s uv sync --locked --no-install-project --inexact --group dev\n' "$_eci_hint_env" >&2
  printf 'For incompatible locked dependencies, prepare a dedicated environment and pass it with --environment:\n  UV_PROJECT_ENVIRONMENT=<path> uv sync --locked --group dev\n' >&2
}

eci_entry_validate() {
  _eci_scope=$1
  _eci_validate_environment || return 1
  _eci_require_persistent_cache || return 1
  case "$_eci_scope" in
    report|version)
      return 0
      ;;
    pytest)
      _eci_require_pytest report || return 1
      ;;
    pytest-noreport)
      _eci_require_pytest no-report || return 1
      ;;
    ruff)
      _eci_require_ruff || return 1
      ;;
    verify)
      _eci_require_pytest report || return 1
      _eci_require_ruff || return 1
      _eci_require_lefthook || return 1
      ;;
  esac
  _eci_sync_check "$_eci_scope" || return $?
  return 0
}

eci_entry_export() {
  _eci_scope=$1
  _eci_native_env=$(_eci_native_path "$ECI_ENV_DIR")
  _eci_native_project=$(_eci_native_path "$ECI_PROJECT_ROOT")
  # shellcheck disable=SC2031
  export UV_PROJECT_ENVIRONMENT="$_eci_native_env"
  # shellcheck disable=SC2031
  export UV_PROJECT="$_eci_native_project"
  case "$_eci_scope" in
    pytest|pytest-noreport|ruff)
      unset PYTHONPATH
      ;;
    *)
      _eci_native_src=$(_eci_native_path "$ECI_SRC_DIR")
      export PYTHONPATH="$_eci_native_src"
      ;;
  esac
  export VIRTUAL_ENV="$_eci_native_env"
  _eci_bin=$(eci_entry_bin_dir)
  if _eci_is_windows && command -v cygpath >/dev/null 2>&1; then
    _eci_bin=$(_eci_unix_path "$_eci_bin")
  fi
  _eci_entry_path="$_eci_bin$(_eci_path_sep)$PATH"
  export PATH="$_eci_entry_path"
  unset UV_CACHE_DIR UV_NO_CACHE UV_CONFIG_FILE UV_NO_CONFIG
  unset UV_NO_DEV UV_NO_GROUP UV_PYTHON UV_PYTHON_PREFERENCE UV_NO_SYNC
  unset UV_PYTHON_DOWNLOADS
}
