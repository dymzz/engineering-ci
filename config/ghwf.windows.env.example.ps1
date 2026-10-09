# Source this file in PowerShell to configure the current shell.
# CLI options override these values. These values override ghwf.local.toml.
$env:GHWF_DOWNLOAD_BACKEND = "auto"
$env:GHWF_DOWNLOAD_CONNECTIONS = "4"
$env:GHWF_DOWNLOAD_TIMEOUT = "30"
$env:GHWF_CONNECT_TIMEOUT = "15"
$env:GHWF_DOWNLOAD_PROCESS_TIMEOUT = "1800"
$env:GHWF_DOWNLOAD_RECOVERY = "restart"
# Optional: leave unset to search PATH; auto falls back to native if missing.
# $env:GHWF_ARIA2C_PATH = "D:/workspace/engineering-ci/.test-tools/aria2/aria2-1.37.0-win-64bit-build1/aria2c.exe"
