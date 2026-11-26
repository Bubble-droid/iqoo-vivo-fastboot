# Shared helpers for the generated vivo/iQOO fastboot package.
# Dot-sourced by flash.ps1, flash-full.ps1, flash-safe.ps1 and fix-oem.ps1.
#
# All scripts run from the package root and expect:
#   package.env                  generated metadata (title, model pattern)
#   firmware-update/             partition images
#   firmware-update/oem/         OEM repair images
#   platform-tools/              optional bundled adb/fastboot
#
# The helpers never embed an absolute host path in the package.

$ErrorActionPreference = 'Stop'

$script:ScriptDir = $PSScriptRoot
if (-not $script:ScriptDir) {
    $script:ScriptDir = (Get-Location).Path
}

$script:ExtraArgs = @()
$script:PackageTitle = 'vivo/iQOO fastboot package'
$script:PackageVersion = ''
$script:ModelRegex = ''
$script:OemIdentity = ''
$script:AvbStripped = $false

$script:DeviceMode = ''
$script:DeviceSerial = ''
$script:DeviceOem = ''
$script:DeviceVersion = ''
$script:DeviceModel = ''
$script:DeviceBattery = ''
$script:ProductModel = ''

function Clear-Screen {
    if (-not [Console]::IsOutputRedirected) {
        try { Clear-Host } catch { }
    }
}

function Pause-ForUser {
    param([string]$Prompt = 'Press Enter to continue...')
    if ([Console]::IsInputRedirected) { return }
    try { Read-Host $Prompt | Out-Null } catch { }
}

function Assert-Interactive {
    if ([Console]::IsInputRedirected) {
        [Console]::Error.WriteLine('Error: run this script in an interactive terminal (confirmation required).')
        exit 1
    }
}

function Confirm-Action {
    param(
        [string]$Prompt,
        [string]$Default = 'n'
    )
    if ($Default -eq 'y') {
        $suffix = ' [Y/n] '
    }
    else {
        $suffix = ' [y/N] '
    }
    while ($true) {
        try {
            $answer = Read-Host ($Prompt + $suffix)
        }
        catch {
            Write-Host ''
            return $false
        }
        if ($null -eq $answer) {
            Write-Host ''
            return $false
        }
        $answer = $answer.Trim()
        if ($answer -eq '') { $answer = $Default }
        if ($answer -match '^(y|yes)$') { return $true }
        if ($answer -match '^(n|no)$') { return $false }
        Write-Host 'Please answer y or n.'
    }
}

function Die {
    param([string]$Message)
    [Console]::Error.WriteLine("-- $Message")
    Pause-ForUser 'Press Enter to exit...'
    exit 1
}

function Set-Title {
    param([string]$Title)
    try { $Host.UI.RawUI.WindowTitle = $Title } catch { }
}

function ConvertFrom-ShellValue {
    param([string]$Value)
    if ($Value.Length -ge 2 -and $Value.StartsWith("'") -and $Value.EndsWith("'")) {
        return $Value.Substring(1, $Value.Length - 2).Replace("'\''", "'")
    }
    return $Value
}

function Read-PackageEnv {
    $path = Join-Path $script:ScriptDir 'package.env'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    foreach ($line in [IO.File]::ReadAllLines($path)) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $Matches[1]
            $value = ConvertFrom-ShellValue $Matches[2]
            switch ($key) {
                'PACKAGE_TITLE' { $script:PackageTitle = $value }
                'PACKAGE_VERSION' { $script:PackageVersion = $value }
                'MODEL_REGEX' { $script:ModelRegex = $value }
                'OEM_IDENTITY' { $script:OemIdentity = $value }
                'AVB_STRIPPED' { $script:AvbStripped = ($value -eq '1') }
            }
        }
    }
}

function Initialize-BundledTools {
    $dir = Join-Path $script:ScriptDir 'platform-tools'
    if (Test-Path -LiteralPath $dir -PathType Container) {
        $env:Path = "$dir;$env:Path"
    }
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Require-Fastboot {
    if (-not (Test-Command 'fastboot')) {
        Die 'fastboot not found in PATH. Install Android platform-tools.'
    }
    if (-not (Test-Command 'adb')) {
        [Console]::Error.WriteLine('Warning: adb not found in PATH. Only Fastboot mode is supported.')
    }
}

function Invoke-Fastboot {
    param([string[]]$Arguments)
    $extra = $script:ExtraArgs
    & fastboot @extra @Arguments
}

function Get-FastbootResult {
    param([string[]]$Arguments)
    $extra = $script:ExtraArgs
    $output = & fastboot @extra @Arguments 2>&1
    $code = $LASTEXITCODE
    $text = (($output | ForEach-Object { "$_" }) -join "`n")
    return [pscustomobject]@{ Text = $text; Code = $code }
}

function Get-FastbootStdout {
    param([string[]]$Arguments)
    $extra = $script:ExtraArgs
    $output = & fastboot @extra @Arguments 2>$null
    return (($output | ForEach-Object { "$_" }) -join "`n")
}

function Invoke-Adb {
    param([string[]]$Arguments)
    $extra = $script:ExtraArgs
    & adb @extra @Arguments
}

function Get-AdbStdout {
    param([string[]]$Arguments)
    $extra = $script:ExtraArgs
    $output = & adb @extra @Arguments 2>$null
    return (($output | ForEach-Object { "$_" }) -join "`n")
}

function Get-PropValue {
    param([string]$Name)
    if (-not (Test-Command 'adb')) { return '' }
    $text = Get-AdbStdout -Arguments @('shell', 'getprop', $Name)
    $lines = @($text -split "`n")
    if ($lines.Count -eq 0) { return '' }
    return $lines[0].Trim()
}

function Get-BatteryLevel {
    if (-not (Test-Command 'adb')) { return '' }
    $text = Get-AdbStdout -Arguments @('shell', 'dumpsys', 'battery')
    $match = [regex]::Match($text, 'level:\s*(\d+)')
    if ($match.Success) { return $match.Groups[1].Value }
    return ''
}

function Get-FastbootDevices {
    $text = Get-FastbootStdout -Arguments @('devices')
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($text -split "`n")) {
        $trimmed = $line.Trim()
        if ($trimmed -ne '') { $items.Add($trimmed) }
    }
    return $items.ToArray()
}

function Get-FirstFastbootDevice {
    $devices = @(Get-FastbootDevices)
    if ($devices.Count -eq 0) { return '' }
    $fields = $devices[0].Trim() -split '\s+'
    return $fields[0]
}

function Get-FirstAdbDevice {
    if (-not (Test-Command 'adb')) { return '' }
    $text = Get-AdbStdout -Arguments @('devices')
    foreach ($line in ($text -split "`n")) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '') { continue }
        $fields = $trimmed -split '\s+'
        if ($fields.Count -ge 2 -and $fields[1] -eq 'device') {
            return $fields[0]
        }
    }
    return ''
}

function Wait-ForFastboot {
    param([int]$TimeoutSeconds = 60)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        if (Get-FirstFastbootDevice) { return $true }
        Start-Sleep -Seconds 1
        $elapsed++
    }
    return $false
}

function Acquire-Device {
    $script:DeviceMode = ''
    $script:DeviceSerial = ''
    $script:DeviceOem = ''
    $script:DeviceVersion = ''
    $script:DeviceModel = ''
    $script:DeviceBattery = ''

    while ($true) {
        if (Test-Command 'adb') {
            $adbSerial = Get-FirstAdbDevice
            if ($adbSerial) {
                $script:DeviceMode = 'adb'
                $script:DeviceSerial = $adbSerial
                $script:DeviceOem = Get-PropValue 'ro.vivo.oem.name'
                $script:DeviceVersion = Get-PropValue 'ro.build.version.release'
                $script:DeviceModel = Get-PropValue 'ro.product.model'
                $script:DeviceBattery = Get-BatteryLevel
                return
            }
        }

        $fastbootSerial = Get-FirstFastbootDevice
        if ($fastbootSerial) {
            $script:DeviceMode = 'fastboot'
            $script:DeviceSerial = $fastbootSerial
            return
        }

        Clear-Screen
        Write-Host ''
        Write-Host ' -- No device connected! Waiting for the phone...'
        Write-Host ' -- Connect it in ADB or Fastboot mode, or press Ctrl+C to abort'
        Start-Sleep -Seconds 2
    }
}

function Show-DeviceInfo {
    if ($script:DeviceMode -eq 'adb') {
        $model = if ($script:DeviceModel) { $script:DeviceModel } else { 'unknown' }
        $android = if ($script:DeviceVersion) { $script:DeviceVersion } else { 'unknown' }
        $oem = if ($script:DeviceOem) { $script:DeviceOem } else { 'unknown' }
        $battery = if ($script:DeviceBattery) { $script:DeviceBattery } else { 'unknown' }
        Write-Host ' -- Device connected in ADB mode:'
        Write-Host " -- serial: $script:DeviceSerial"
        Write-Host " -- model: $model"
        Write-Host " -- Android: $android"
        Write-Host " -- OEM: $oem"
        Write-Host " -- battery: $battery%"
        if ($script:DeviceBattery -match '^\d+$' -and [int]$script:DeviceBattery -lt 35) {
            [Console]::Error.WriteLine(' -- Warning: battery is below 35%. Charge the phone before flashing.')
        }
    }
    elseif ($script:DeviceMode -eq 'fastboot') {
        Write-Host ' -- Device connected in Fastboot mode:'
        Write-Host " -- $script:DeviceSerial"
    }
}

# Kept for older callers; see Enter-Bootloader.
function Enter-Fastboot {
    Enter-Bootloader
}

function Test-Fastbootd {
    $result = Get-FastbootResult -Arguments @('getvar', 'is-userspace')
    return $result.Text -match 'is-userspace:\s*yes'
}

# Put the phone into the bootloader, whichever mode it started in.  adb and
# fastbootd both have to leave userspace before the physical partitions can be
# written; a phone already in the bootloader stays where it is.
function Enter-Bootloader {
    switch ($script:DeviceMode) {
        'adb' {
            Write-Host ' -- Rebooting into the bootloader...'
            Invoke-Adb -Arguments @('reboot', 'bootloader')
            if ($LASTEXITCODE -ne 0) { Die 'reboot error' }
            if (-not (Wait-ForFastboot -TimeoutSeconds 90)) {
                Die 'No devices are connected! Flashing aborted.'
            }
        }
        'fastboot' {
            if (Test-Fastbootd) {
                Write-Host ' -- Rebooting from fastbootd to the bootloader...'
                Invoke-Fastboot -Arguments @('reboot', 'bootloader')
                if ($LASTEXITCODE -ne 0) { Die 'reboot error' }
                if (-not (Wait-ForFastboot -TimeoutSeconds 90)) {
                    Die 'No devices are connected! Flashing aborted.'
                }
            }
        }
    }
}

function Check-Unlocked {
    $result = Get-FastbootResult -Arguments @('getvar', 'unlocked')
    if ($result.Text -notmatch 'unlocked:\s*yes') {
        [Console]::Error.WriteLine('-- Device is locked. Unlock the bootloader first.')
        Pause-ForUser
        exit 1
    }
}

function Check-Model {
    $result = Get-FastbootResult -Arguments @('oem', 'product-model')
    $lines = @(($result.Text -split "`n") | Where-Object { $_.Trim() -ne '' })
    $first = if ($lines.Count -gt 0) { $lines[0].Trim() } else { '' }
    if ($first -match ':') {
        $script:ProductModel = ($first -replace '^[^:]*:\s*', '').Trim()
    }
    else {
        $script:ProductModel = $first
    }

    if (-not $script:ProductModel) {
        [Console]::Error.WriteLine(' -- Warning: could not read the product model.')
        if ($env:SKIP_MODEL_CHECK -ne '1') {
            Die 'Device model check failed. Set SKIP_MODEL_CHECK=1 to override.'
        }
        return
    }

    if (-not $script:ModelRegex) {
        [Console]::Error.WriteLine(' -- Warning: package.env has no MODEL_REGEX; model check skipped.')
        return
    }

    if (-not [regex]::IsMatch($script:ProductModel, $script:ModelRegex)) {
        [Console]::Error.WriteLine(" -- Detected model: $script:ProductModel")
        [Console]::Error.WriteLine(" -- Expected model(s): $script:ModelRegex")
        if ($env:SKIP_MODEL_CHECK -ne '1') {
            Die 'Device check failed. This package does not target this model.'
        }
        [Console]::Error.WriteLine(' -- SKIP_MODEL_CHECK=1 set; continuing anyway.')
    }
}

function Write-DeviceInfo {
    $oem = if ($script:DeviceOem) { $script:DeviceOem } else { $script:OemIdentity }
    $model = if ($script:ProductModel) { $script:ProductModel } else { $script:DeviceModel }
    $lines = @(
        "product-model=$model",
        "oem=$oem",
        "android=$script:DeviceVersion",
        "package=$script:PackageVersion"
    )
    $path = Join-Path $script:ScriptDir 'device-info.txt'
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($path, (($lines -join "`r`n") + "`r`n"), $encoding)
}

function Flash-Part {
    param([string]$Partition, [string]$Image)
    if (-not (Test-Path -LiteralPath $Image -PathType Leaf)) {
        [Console]::Error.WriteLine("-- File not found: $Image")
        Pause-ForUser 'Press Enter to exit...'
        exit 1
    }
    Write-Host " -- $Partition <- $Image"
    Invoke-Fastboot -Arguments @('flash', $Partition, $Image)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("-- Flash $Partition failed.")
        Pause-ForUser 'Press Enter to exit...'
        exit 1
    }
}

function Test-PreflightItems {
    param([string[]]$Items)
    $missing = $false
    foreach ($item in $Items) {
        $fields = $item -split '\|'
        $part = $fields[0]
        $image = $fields[1]
        $optional = $fields.Count -ge 3 -and $fields[2] -eq 'optional'
        if (-not (Test-Path -LiteralPath $image -PathType Leaf)) {
            if ($optional) {
                Write-Host " -- Optional image missing, will skip: $part ($image)"
            }
            else {
                [Console]::Error.WriteLine("-- Required image missing: $image")
                $missing = $true
            }
        }
    }
    if ($missing) { Die 'Package is incomplete.' }
}

function Flash-Items {
    param([string[]]$Items)
    foreach ($item in $Items) {
        $fields = $item -split '\|'
        $part = $fields[0]
        $image = $fields[1]
        $optional = $fields.Count -ge 3 -and $fields[2] -eq 'optional'
        if ($optional -and -not (Test-Path -LiteralPath $image -PathType Leaf)) {
            Write-Host " -- Skipping ${part}: $image not found"
            continue
        }
        Flash-Part -Partition $part -Image $image
    }
}

function Reboot-Fastboot {
    Invoke-Fastboot -Arguments @('reboot', 'fastboot')
    if ($LASTEXITCODE -ne 0) { Die 'reboot error' }
    if (-not (Wait-ForFastboot -TimeoutSeconds 90)) {
        Die 'No devices are connected! Flashing aborted.'
    }
}

function Repair-Oem {
    $oem = if ($script:DeviceOem) { $script:DeviceOem } else { $script:OemIdentity }
    Write-Host ''
    if (-not $oem) {
        Write-Host ' -- OEM identity unknown; skipping automatic OEM repair.'
        return
    }

    Write-Host " -- OEM repair target: $oem"
    $vbmeta = "firmware-update/oem/vbmeta_oem_$oem.img"
    $image = "firmware-update/oem/OEM_$oem.img"
    $hasVbmeta = Test-Path -LiteralPath $vbmeta -PathType Leaf
    $hasImage = Test-Path -LiteralPath $image -PathType Leaf

    if (-not $hasVbmeta -and -not $hasImage) {
        Write-Host " -- No OEM repair image for $oem; run .\fix-oem.cmd after boot to select an identity."
        return
    }

    if (-not (Confirm-Action -Prompt "Restore the OEM identity ($oem) now?" -Default 'y')) {
        Write-Host ' -- Skipping OEM repair. Run .\fix-oem.cmd after boot if the identity is wrong.'
        return
    }

    if ($hasVbmeta) {
        Flash-Part -Partition 'vbmeta_oem' -Image $vbmeta
        Reboot-Fastboot
    }
    else {
        Write-Host " -- $vbmeta not found; keeping the vbmeta_oem already flashed."
    }

    if ($hasImage) {
        Flash-Part -Partition 'oem' -Image $image
    }
    else {
        Write-Host " -- $image not found; run .\fix-oem.cmd after boot to select an identity."
    }
}

# Wipe only the data partitions.  The boot chain, super, modem and OEM images
# written above stay in place, so the installed system remains complete.
#
# The default path uses `fastboot erase` only.  `fastboot -w` makes the host
# build a f2fs image as large as the userdata partition and then sends it as a
# sparse image; some vivo bootloaders (notably the A15 one) stall on that
# transfer, leave a half-formatted filesystem behind and force a bootloop.
# Android formats an empty userdata on first boot, so erasing is enough.
# Set WIPE_WITH_FASTBOOT_W=1 to use the old `fastboot -w` path explicitly.
function Clear-UserData {
    Write-Host ''
    Write-Host ' -- Wiping data partitions (system partitions are kept).'
    $ok = $false

    # fastbootd only exposes the logical partitions that live inside super.
    # Wiping there reports "wipe task partition not found" for userdata,
    # cache and metadata but still exits 0, so switch to the bootloader,
    # which owns those physical partitions.
    if (Test-Fastbootd) {
        Write-Host ' -- Rebooting from fastbootd to the bootloader for the wipe...'
        Invoke-Fastboot -Arguments @('reboot', 'bootloader') | Out-Null
        Wait-ForFastboot 60 | Out-Null
    }

    if ($env:WIPE_WITH_FASTBOOT_W -eq '1') {
        $result = Get-FastbootResult -Arguments @('-w')
        if ($result.Text) { Write-Host $result.Text }
        if ($result.Code -eq 0 -and $result.Text -notmatch 'partition not found') {
            $ok = $true
        }
        else {
            Write-Host ' -- fastboot -w could not wipe the data partitions; using fastboot erase instead.'
            $erase = Get-FastbootResult -Arguments @('erase', 'userdata')
            if ($erase.Code -eq 0) { $ok = $true }
        }
    }
    else {
        $erase = Get-FastbootResult -Arguments @('erase', 'userdata')
        if ($erase.Code -eq 0) { $ok = $true }
        else { Write-Host ' -- note: could not erase userdata' }
    }

    # metadata stores encryption and update state; cache and frp are optional
    # on newer layouts.  `misc` is left alone because it can carry the A/B
    # slot suffix.
    foreach ($part in @('metadata', 'cache', 'frp')) {
        $result = Get-FastbootResult -Arguments @('erase', $part)
        if ($result.Code -ne 0) {
            Write-Host " -- note: could not erase $part (absent on this device?)"
        }
    }

    if (-not $ok) {
        [Console]::Error.WriteLine(' -- Warning: user data was not erased. The new system may stop at')
        [Console]::Error.WriteLine(' -- the encryption screen; wipe data manually or flash again.')
        return $false
    }
    return $true
}

# Ask before rebooting whether to start the new system with clean user data.
function Ask-UserDataWipe {
    Write-Host ''
    Write-Host ' -- All flashing tasks are complete.'
    Write-Host ' -- Wiping now gives the new system a clean first boot:'
    Write-Host ' --   userdata: apps, photos, accounts, downloads, internal storage'
    Write-Host ' --   metadata: encryption and update state'
    Write-Host ' --   cache, frp'
    Write-Host ' -- The system, boot, vendor, super, modem and OEM partitions stay intact.'
    Write-Host ' -- This cannot be undone.'
    Write-Host ''
    if (-not (Confirm-Action -Prompt 'Erase all user data before rebooting?' -Default 'n')) {
        Write-Host ' -- Keeping existing data.'
        return
    }
    Clear-UserData | Out-Null
}

function Final-Reboot {
    Write-Host ''
    if (-not (Confirm-Action -Prompt 'Reboot the phone now?' -Default 'y')) {
        Write-Host ' -- Staying in Fastboot mode.'
        Write-Host ' -- Reboot manually when ready: fastboot reboot'
        return
    }
    Write-Host ' -- Flashing finished. Rebooting...'
    Invoke-Fastboot -Arguments @('reboot')
    if ($LASTEXITCODE -ne 0) { Die 'reboot error' }
}

Initialize-BundledTools
