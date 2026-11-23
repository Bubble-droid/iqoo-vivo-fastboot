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

$items = @(
    "boot|firmware-update/boot.img"
    "modem|firmware-update/NON-HLOS.bin|optional"
    "dsp|firmware-update/dspso.bin|optional"
    "bluetooth|firmware-update/BTFM.bin|optional"
    "dtbo|firmware-update/dtbo.img"
    "qupfw|firmware-update/qupv3fw.elf|optional"
    "keymaster|firmware-update/keymint.mbn|optional"
    "uefisecapp|firmware-update/uefi_sec.mbn|optional"
    "uefi|firmware-update/uefi.elf|optional"
    "shrm|firmware-update/shrm.elf|optional"
    "multiimgoem|firmware-update/multi_image.mbn|optional"
    "multiimgqti|firmware-update/multi_image_qti.mbn|optional"
    "cpucp|firmware-update/cpucp.elf|optional"
    "abl|firmware-update/abl.elf"
    "tz|firmware-update/tz.mbn"
    "aop|firmware-update/aop.mbn"
    "aop_config|firmware-update/aop_devcfg.mbn|optional"
    "imagefv|firmware-update/imagefv.elf|optional"
    "hyp|firmware-update/hypvm.mbn"
    "xbl_config|firmware-update/xbl_config.elf"
    "xbl|firmware-update/xbl_s.melf"
    "storage|firmware-update/firmware.bin|optional"
    "featenabler|firmware-update/featenabler.mbn|optional"
    "vbmeta|firmware-update/vbmeta.img"
    "vbmeta_system|firmware-update/vbmeta_system.img|optional"
    "vendor_boot|firmware-update/vendor_boot.img"
    "recovery|firmware-update/recovery.img|optional"
    "toolsfv|firmware-update/tools.fv|optional"
    "xbl_ramdump|firmware-update/XblRamdump.elf|optional"
    "storsec|firmware-update/storsec.mbn|optional"
    "devcfg|firmware-update/devcfg_overlay1.mbn|optional"
    "devcfg|firmware-update/devcfg.mbn"
    "vm-bootsys|firmware-update/vm-bootsys.img|optional"
    "vbmeta_vgc|firmware-update/vbmeta_vgc.img|optional"
    "super|firmware-update/super.img"
)

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
