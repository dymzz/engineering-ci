<#
.SYNOPSIS
    Script that deliberately trips no default PSScriptAnalyzer rule.

.DESCRIPTION
    Full cmdlet names, no Write-Host, no unused assignment and no aliases, so
    the positive case stays clean without suppressing anything.
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
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $items = Get-ChildItem -LiteralPath $Path -File
    Write-Output ("Found {0} item(s)" -f $items.Count)
}

Export-ModuleMember -Function Get-ReportName, Invoke-ReportSummary