[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ErrorActionPreference = "Stop"

function Find-GitBash {
    if ($env:ECI_GIT_BASH -and (Test-Path -LiteralPath $env:ECI_GIT_BASH)) {
        return $env:ECI_GIT_BASH
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
        foreach ($candidate in @(
            (Join-Path $gitRoot "bin\bash.exe"),
            (Join-Path $gitRoot "usr\bin\bash.exe")
        )) {
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
        $execPath = & $git.Source --exec-path 2>$null
        if ($execPath) {
            $root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $execPath))
            foreach ($candidate in @(
                (Join-Path $root "bin\bash.exe"),
                (Join-Path $root "usr\bin\bash.exe")
            )) {
                if (Test-Path -LiteralPath $candidate) {
                    return $candidate
                }
            }
        }
    }
    throw "Git Bash was not found. Install Git for Windows or set ECI_GIT_BASH to bash.exe."
}

$bash = Find-GitBash
$script = Join-Path $PSScriptRoot "engineering-ci.sh"
if (-not (Test-Path -LiteralPath $script)) {
    throw "Missing engineering-ci.sh next to $PSCommandPath"
}

& $bash $script @Arguments
exit $LASTEXITCODE
