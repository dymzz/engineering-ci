#!/bin/sh

SCRIPT_DIR=$(dirname -- "$0")
SCRIPT_DIR=$(cd -- "$SCRIPT_DIR" && pwd -P)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/eci-env.sh"

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
