<#
.SYNOPSIS
    PowerShell module used to prove .psm1 files are covered by the same scan.

.DESCRIPTION
    Module files are a distinct suffix from .ps1 and must be selected by the
    same check rather than silently skipped.

    Export-ModuleMember is deliberately not used here. Observed against
    PSScriptAnalyzer 1.9.0: a script that calls it can make the analyzer
    fail with an execution error instead of reporting diagnostics, and
    whether it does depends on the analysed file and the session state. A
    committed fixture must not depend on that, so this module exports
    nothing and exists only to prove the suffix is selected.
#>
function Get-HelperName {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$BaseName
    )

    return "$BaseName-helper"
}
