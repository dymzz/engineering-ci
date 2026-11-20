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
        files_scanned             = 0
        powershell_version        = "$($PSVersionTable.PSVersion)"
        psscriptanalyzer_version  = ''
    }
}

$version = ''
$moduleRoot = ''
$paths = New-Object System.Collections.Generic.List[string]
$settings = ''
$severity = ''
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        '-Version' {
            if ($i + 1 -ge $args.Count) { Write-Failure 'Missing value for -Version'; exit 3 }
            $version = $args[++$i]; continue
        }
        '-ModuleRoot' {
            if ($i + 1 -ge $args.Count) { Write-Failure 'Missing value for -ModuleRoot'; exit 3 }
            $moduleRoot = $args[++$i]; continue
        }
        '-Path' {
            if ($i + 1 -ge $args.Count) { Write-Failure 'Missing value for -Path'; exit 3 }
            $paths.Add($args[++$i]); continue
        }
        '-Settings' {
            if ($i + 1 -ge $args.Count) { Write-Failure 'Missing value for -Settings'; exit 3 }
            $settings = $args[++$i]; continue
        }
        '-Severity' {
            if ($i + 1 -ge $args.Count) { Write-Failure 'Missing value for -Severity'; exit 3 }
            $severity = $args[++$i]; continue
        }
        default { Write-Failure "Unknown argument: $($args[$i])"; exit 3 }
    }
}

if ($paths.Count -eq 0) {
    Write-Failure 'At least one -Path is required'
    exit 3
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

Write-Record @{
    schema_version           = 1
    status                    = 'ok'
    diagnostics               = @()
    files_scanned             = $files.Count
    powershell_version        = "$($PSVersionTable.PSVersion)"
    psscriptanalyzer_version  = "$((Get-Module PSScriptAnalyzer).Version)"
}

foreach ($file in $files) {
    $invoke = @{ Path = $file }
    if ($settings) { $invoke['Settings'] = $settings }
    if ($severity) { $invoke['Severity'] = $severity }
    try {
        foreach ($record in @(Invoke-ScriptAnalyzer @invoke)) {
            Write-Record @{
                rule     = "$($record.RuleName)"
                severity = "$($record.Severity)"
                message  = "$($record.Message)"
                script   = "$($record.ScriptName)"
                line     = $record.Line
                column   = $record.Column
            }
        }
    } catch {
        Write-Failure "Analyzing $file failed: $($_.Exception.Message)"
        exit 3
    }
}

exit 0