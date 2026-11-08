# Invokes PSScriptAnalyzer for one explicit set of paths and reports what the
# analyzer actually returned. It parses the named scripts; it never executes
# them and never requests an automatic fix.
#
# One JSON object per line: the first line is the envelope, every following
# line is one diagnostic. A single JSON document would collapse a one-element
# diagnostic array, so the records stay independent and Python reads them line
# by line.
#
# -ModuleRoot is the directory that directly contains PSScriptAnalyzer.psd1,
# resolved by the caller rather than through PSModulePath.
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Record($value) {
    [Console]::Out.WriteLine(($value | ConvertTo-Json -Depth 6 -Compress))
}

function Write-Failure($message) {
    Write-Record @{
        schema_version           = 1
        status                    = 'error'
        error                     = "$message"
        diagnostics               = @()
        candidates                = if ($files) { $files.Count } else { 0 }
        files_scanned             = if ($analyzed) { $analyzed.Count } else { 0 }
        powershell_version        = "$($PSVersionTable.PSVersion)"
        psscriptanalyzer_version  = try { "$((Get-Module PSScriptAnalyzer).Version)" } catch { '' }
    }
}

$version = ''
$moduleRoot = ''
$paths = New-Object System.Collections.Generic.List[string]
$settings = ''
$severity = ''
# One -Path carries every path that follows it, so the caller can pass a
# whole script set in one argument. The index is managed here rather than by
# the loop, because a single flag now consumes more than one value.
$i = 0
while ($i -lt $args.Count) {
    $argument = $args[$i]
    switch ($argument) {
        '-Version' { $i++; $version = $args[$i]; $i++; continue }
        '-ModuleRoot' { $i++; $moduleRoot = $args[$i]; $i++; continue }
        '-Path' {
            $i++
            while ($i -lt $args.Count -and -not $args[$i].StartsWith('-')) {
                $paths.Add($args[$i])
                $i++
            }
            continue
        }
        '-Settings' { $i++; $settings = $args[$i]; $i++; continue }
        '-Severity' { $i++; $severity = $args[$i]; $i++; continue }
        default { Write-Failure "Unknown argument: $argument"; exit 3 }
    }
}

# Import from the explicit directory rather than PSModulePath so the resolved
# module cannot depend on whatever else the image happens to ship.
try {
    $manifest = Join-Path $moduleRoot 'PSScriptAnalyzer.psd1'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw "PSScriptAnalyzer $version is not present under $moduleRoot"
    }
    Import-Module $manifest -Force -ErrorAction Stop
} catch {
    Write-Failure "PSScriptAnalyzer $version is unavailable: $($_.Exception.Message)"
    exit 3
}

$files = New-Object System.Collections.Generic.List[string]
try {
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Container) {
            Get-ChildItem -LiteralPath $path -Recurse -File -Include '*.ps1', '*.psm1' -ErrorAction Stop |
                ForEach-Object { $files.Add($_.FullName) }
        }
        elseif (Test-Path -LiteralPath $path -PathType Leaf) {
            $files.Add((Get-Item -LiteralPath $path).FullName)
        }
        else {
            throw "Input path not found: $path"
        }
    }
} catch {
    Write-Failure $_.Exception.Message
    exit 3
}

$analyzed = New-Object System.Collections.Generic.List[string]
$diagnostics = New-Object System.Collections.Generic.List[object]

foreach ($file in $files) {
    $invoke = @{ Path = $file }
    if ($settings) { $invoke['Settings'] = $settings }
    if ($severity) { $invoke['Severity'] = $severity }
    try {
        foreach ($record in @(Invoke-ScriptAnalyzer @invoke)) {
            $diagnostics.Add(@{
                rule     = "$($record.RuleName)"
                severity = "$($record.Severity)"
                message  = "$($record.Message)"
                script   = "$($record.ScriptName)"
                line     = $record.Line
                column   = $record.Column
            })
        }
        $analyzed.Add($file)
    } catch {
        Write-Failure "Analyzing $file failed: $($_.Exception.Message)"
        exit 3
    }
}

Write-Record @{
    schema_version           = 1
    status                    = 'ok'
    diagnostics               = @()
    candidates                = $files.Count
    files_scanned             = $analyzed.Count
    powershell_version        = "$($PSVersionTable.PSVersion)"
    psscriptanalyzer_version  = "$((Get-Module PSScriptAnalyzer).Version)"
}

foreach ($diag in $diagnostics) {
    Write-Record $diag
}

exit 0