# Full fastboot flashing script for a generated vivo/iQOO package.
#
# Writes every partition produced by ota2fastboot.py, including the bootloader
# chain (xbl, abl, tz, hyp, ...) and the UFS firmware (storage).  An interrupted
# bootloader write can leave the phone unbootable without EDL recovery.  Use
# .\flash.ps1 when you want an AP-only flash that keeps fastboot reachable.

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
Set-Location -LiteralPath $script:ScriptDir

. (Join-Path $PSScriptRoot 'common.ps1')
$script:ExtraArgs = @($args)
Read-PackageEnv

Require-Fastboot
Assert-Interactive
$title = if ($script:PackageTitle) { $script:PackageTitle } else { 'vivo/iQOO fastboot package' }
Set-Title $title

$listPath = 'firmware-update/flash-items-full.txt'
if (-not (Test-Path -LiteralPath $listPath -PathType Leaf)) {
    Die 'firmware-update/flash-items-full.txt not found; package is incomplete'
}
$items = @(Get-Content -LiteralPath $listPath | Where-Object { $_ -and -not $_.StartsWith('#') })

Test-PreflightItems -Items $items

Write-Host ''
Write-Host ' -- This is a FULL flash: bootloader and UFS firmware will be written.'
Write-Host ' -- An interrupted bootloader write can make the phone unrecoverable'
Write-Host ' -- without EDL. Keep the USB cable connected and do not touch the phone.'
Write-Host ' -- For an AP-only flash that keeps fastboot reachable, use .\flash.ps1.'
Write-Host ''
Write-Host ' -- Before use, make sure the phone is connected and either booted with'
Write-Host ' -- USB debugging enabled, or already in Fastboot mode.'
Write-Host ' -- In ADB mode the script records the real OEM identity first, so the'
Write-Host ' -- matching OEM partition can be restored after flashing.'
Write-Host ''
if (-not (Confirm-Action -Prompt 'Start flashing now?' -Default 'y')) {
    Write-Host '-- Canceled.'
    exit 0
}

Clear-Screen
Acquire-Device
Clear-Screen
Show-DeviceInfo
Enter-Fastboot

Clear-Screen
Write-Host ''
Check-Unlocked
Check-Model
Write-DeviceInfo

Write-Host ' -- Device unlocked'
Write-Host ' -- Device check passed'
Write-Host ''
Write-Host '-------------------------------------------'
Start-Sleep -Seconds 1
Clear-Screen
Write-Host ''

Flash-Items -Items $items
Repair-Oem
Final-Reboot
Pause-ForUser
