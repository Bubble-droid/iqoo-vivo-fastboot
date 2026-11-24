# Default fastboot flashing script for a generated vivo/iQOO package.
#
# Writes only Android partitions (boot chain + dynamic super) and the AVB
# metadata that belongs to them.  It never writes:
#   xbl, xbl_config, abl, tz, hyp, aop, devcfg, keymaster, uefisecapp,
#   uefi, shrm, cpucp, imagefv, multiimg*, storsec, featenabler, toolsfv,
#   xbl_ramdump, storage (UFS firmware), modem, dsp, bluetooth, qupfw.
#
# If a write fails, the bootloader and fastboot are still intact, so the phone
# can normally be recovered by flashing the previous package.
# Use .\flash-full.ps1 for a complete flash that includes the bootloader chain
# and the UFS firmware.
# After every write, the script asks whether to erase user data before rebooting.
# Set $env:FLASH_SAFE_WITH_MODEM = "1" to include the modem group as well.

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
Set-Location -LiteralPath $script:ScriptDir

. (Join-Path $PSScriptRoot 'common.ps1')
$script:ExtraArgs = @($args)
Read-PackageEnv

Require-Fastboot
Assert-Interactive
$title = if ($script:PackageTitle) { $script:PackageTitle } else { 'vivo/iQOO fastboot package' }
Set-Title "$title (safe)"

$safeList = 'firmware-update/flash-items-safe.txt'
if (-not (Test-Path -LiteralPath $safeList -PathType Leaf)) {
    Die 'firmware-update/flash-items-safe.txt not found; package is incomplete'
}
$items = @(Get-Content -LiteralPath $safeList | Where-Object { $_ -and -not $_.StartsWith('#') })

if ($env:FLASH_SAFE_WITH_MODEM -eq '1') {
    $modemList = 'firmware-update/flash-items-modem.txt'
    if (Test-Path -LiteralPath $modemList -PathType Leaf) {
        $items += @(Get-Content -LiteralPath $modemList | Where-Object { $_ -and -not $_.StartsWith('#') })
    }
    Write-Host ' -- FLASH_SAFE_WITH_MODEM=1: the modem group will be written too.'
}

Test-PreflightItems -Items $items

Write-Host ''
Write-Host ' -- SAFE flash: Android partitions only.'
Write-Host ' -- The bootloader, bootloader configuration, UFS firmware and modem'
Write-Host ' -- firmware are NOT touched, so fastboot remains reachable if this'
Write-Host ' -- flash fails or the new system does not boot.'
Write-Host ' -- The currently installed bootloader stays in place. If the new system'
Write-Host ' -- requires a newer bootloader, it may stop at the boot logo; recover by'
Write-Host ' -- flashing the previous package.'
Write-Host ''
if (-not (Confirm-Action -Prompt 'Start safe flashing now?' -Default 'y')) {
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
Ask-UserDataWipe
Final-Reboot
Pause-ForUser
