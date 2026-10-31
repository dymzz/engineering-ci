<#
.SYNOPSIS
    PowerShell module used to prove .psm1 files are covered by the same scan.

.DESCRIPTION
    Module files are a distinct suffix from .ps1 and must be selected by the
    same check rather than silently skipped.
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

Export-ModuleMember -Function Get-HelperName