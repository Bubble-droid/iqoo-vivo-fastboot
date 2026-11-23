# Verify the package against manifest.sha256.
#
# The manifest covers every packaged file except the manifest files themselves
# and meta/build.log.  The log is excluded because it is still being written
# while the manifest is generated.
#
# Usage:
#   .\verify.ps1            verify every listed file
#   .\verify.ps1 --quiet    print only failures and the final status

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
Set-Location -LiteralPath $script:ScriptDir

$Manifest = 'manifest.sha256'
$Quiet = $false

$rawArgs = @($args)
if ($rawArgs.Count -eq 1 -and $rawArgs[0] -in @('--quiet', '-q')) {
    $Quiet = $true
}
elseif ($rawArgs.Count -gt 0) {
    [Console]::Error.WriteLine('Usage: .\verify.ps1 [--quiet]')
    exit 2
}

if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    [Console]::Error.WriteLine("-- $Manifest not found. This package cannot be verified.")
    exit 1
}

$lines = @([IO.File]::ReadAllLines((Join-Path $script:ScriptDir $Manifest)) |
    Where-Object { $_.Trim() -ne '' })
$count = $lines.Count

if (-not $Quiet) {
    Write-Host " -- Verifying $count files against $Manifest..."
}

$failed = 0
foreach ($line in $lines) {
    if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') {
        [Console]::Error.WriteLine("-- Invalid manifest line: $line")
        $failed++
        continue
    }
    $expected = $Matches[1].ToLowerInvariant()
    $relative = $Matches[2]
    if (-not (Test-Path -LiteralPath $relative -PathType Leaf)) {
        [Console]::Error.WriteLine("-- Missing file: $relative")
        $failed++
        continue
    }
    $actual = (Get-FileHash -LiteralPath $relative -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        [Console]::Error.WriteLine("-- Checksum mismatch: $relative")
        $failed++
    }
    elseif (-not $Quiet) {
        Write-Host " -- OK: $relative"
    }
}

if ($failed -gt 0) {
    [Console]::Error.WriteLine("-- Verification failed ($failed of $count files).")
    exit 1
}

Write-Host " -- Package OK ($count files)."
