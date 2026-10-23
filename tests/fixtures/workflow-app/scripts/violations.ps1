<#
.SYNOPSIS
    Script carrying confirmed default-rule violations for the negative case.

.DESCRIPTION
    Each marked statement trips one default PSScriptAnalyzer rule so the
    expected diagnostics are unambiguous.
#>

function Get-UnusedAssignment {
    # PSUseDeclaredVarsMoreThanAssignments: never read afterwards.
    $neverRead = 'assigned and discarded'
    return 'done'
}

function Invoke-AliasUsage {
    # PSAvoidUsingCmdletAliases: ls instead of Get-ChildItem.
    ls -Path $PWD.Path
    # PSAvoidUsingWriteHost: Write-Host instead of the output stream.
    Write-Host 'status line'
}