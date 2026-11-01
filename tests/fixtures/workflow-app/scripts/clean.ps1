<#
.SYNOPSIS
    Script that deliberately trips no default PSScriptAnalyzer rule.

.DESCRIPTION
    Full cmdlet names, no Write-Host, no unused assignment and no aliases, so
    the positive case stays clean without suppressing anything.

    Observed against PSScriptAnalyzer 1.9.0: Export-ModuleMember is only
    meaningful in a module, and a .ps1 file that declares it makes the
    analyzer fail with a null reference instead of reporting diagnostics.
    A SupportsShouldProcess attribute without a ShouldProcess call is
    reported as PSShouldProcess. Neither belongs in a clean positive case.
#>
function Get-ReportName {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$BaseName
    )

    return "$BaseName-report"
}

function Invoke-ReportSummary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $items = Get-ChildItem -LiteralPath $Path -File
    Write-Output ("Found {0} item(s)" -f $items.Count)
}
