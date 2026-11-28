#!/bin/sh

# Thin local engineering-ci entry. It resolves and validates the host
# environment before any operation, then delegates to the bound interpreter or
# native tool. It never creates or synchronizes an environment.

SCRIPT_DIR=$(dirname -- "$0")
SCRIPT_DIR=$(cd -- "$SCRIPT_DIR" && pwd -P)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/eci-env.sh"

usage() {
  cat <<'EOF'
Usage: engineering-ci.sh [--environment <path>] <operation> [arguments...]

Operations:
  verify [arguments...]   run engineering-ci verify through the bound environment
  cli <arguments...>      run the engineering-ci CLI through the bound environment
  pytest [arguments...]   run pytest through the bound environment
  ruff [arguments...]     run ruff through the bound environment

The default environment is the primary managing checkout's .venv for a linked
worktree, or the current checkout's .venv in the primary checkout. Pass
--environment to select an explicitly prepared environment. No environment is
created or synchronized by this entry; readiness failures stop before the
operation and print the maintenance commands.
EOF
}

environment=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --environment)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        printf 'engineering-ci entry: --environment requires a path\n' >&2
        exit 2
      fi
      environment=$2
      shift 2
      ;;
    --environment=*)
      environment=${1#*=}
      if [ -z "$environment" ]; then
        printf 'engineering-ci entry: --environment requires a path\n' >&2
        exit 2
      fi
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      printf 'engineering-ci entry: unknown option %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [ "$#" -lt 1 ]; then
  usage >&2
  exit 2
fi

operation=$1
shift

case "$operation" in
  verify)
    case "${1:-}" in
      -h|--help) scope=runtime ;;
      *) scope=verify ;;
    esac
    ;;
  cli)
    if [ "${1:-}" = "verify" ]; then
      case "${2:-}" in
        report|-h|--help) scope=runtime ;;
        *) scope=verify ;;
      esac
    else
      scope=runtime
    fi
    ;;
  pytest)
    scope=pytest
    ;;
  ruff)
    scope=ruff
    ;;
  *)
    printf 'engineering-ci entry: unknown operation %s\n' "$operation" >&2
    usage >&2
    exit 2
    ;;
esac

if [ -n "${UV_CACHE_DIR:-}" ] || [ -n "${UV_NO_CACHE:-}" ] || [ -n "${UV_CONFIG_FILE:-}" ] || [ -n "${UV_NO_CONFIG:-}" ]; then
  _eci_warn "ignoring inherited uv cache/config overrides for the normal entry; use host configuration or maintenance commands"
fi

eci_entry_resolve normal "$environment" || exit $?
eci_entry_validate "$scope" || exit $?
eci_entry_export "$scope"

case "$operation" in
  verify)
    exec "$ECI_PYTHON" -P -m engineering_ci.cli verify "$@"
    ;;
  cli)
    exec "$ECI_PYTHON" -P -m engineering_ci.cli "$@"
    ;;
  pytest)
    exec "$ECI_PYTEST_BIN" "$@"
    ;;
  ruff)
    exec "$ECI_RUFF_BIN" "$@"
    ;;
esac
