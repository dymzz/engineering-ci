# Source this file in a POSIX shell to configure the current shell.
# CLI options override these values. These values override ghwf.local.toml.
export GHWF_DOWNLOAD_BACKEND="auto"
export GHWF_DOWNLOAD_CONNECTIONS="4"
export GHWF_DOWNLOAD_TIMEOUT="30"
export GHWF_CONNECT_TIMEOUT="15"
export GHWF_DOWNLOAD_PROCESS_TIMEOUT="1800"
export GHWF_DOWNLOAD_RECOVERY="restart"
# Optional: leave unset to search PATH; auto falls back to native if missing.
# export GHWF_ARIA2C_PATH="/usr/bin/aria2c"
