# Compatibility alias for packages built before the script rename.
#
# flash.ps1 is now the safe, Android-only entry point.  This wrapper keeps
# older instructions and external callers working.

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
$global:LASTEXITCODE = 0
& (Join-Path $script:ScriptDir 'flash.ps1') @args
exit $LASTEXITCODE
