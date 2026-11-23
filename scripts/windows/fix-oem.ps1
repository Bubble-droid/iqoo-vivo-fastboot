# Repair a vivo/iQOO OEM identity after flashing.
#
# Usage:
#   .\fix-oem.ps1                     interactive menu
#   .\fix-oem.ps1 --list              list available OEM images
#   .\fix-oem.ps1 --name PD2171_CN-ZH_FULL_SC_NULL
#   .\fix-oem.ps1 --name BMW --yes    non-interactive, skip confirmation
#   .\fix-oem.ps1 --filter CT-BMW
#   .\fix-oem.ps1 -s <serial>         extra arguments go to fastboot
#
# The package stores the OEM variants selected at build time: the variant used
# for super by default, or every variant with --all-oem.  The correct identity
# can be restored even when the phone reports a damaged system.

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
Set-Location -LiteralPath $script:ScriptDir

$OemDir = 'firmware-update/oem'
$SelectName = ''
$FilterText = ''
$AssumeYes = $false
$ListOnly = $false
$script:ExtraArgs = @()

$rawArgs = @($args)
for ($i = 0; $i -lt $rawArgs.Count; $i++) {
    switch ($rawArgs[$i]) {
        { $_ -in '--list', '-l' } {
            $ListOnly = $true
        }
        { $_ -in '--name', '-n' } {
            if ($i + 1 -ge $rawArgs.Count) {
                [Console]::Error.WriteLine("Error: $($rawArgs[$i]) needs a value.")
                exit 2
            }
            $i++
            $SelectName = $rawArgs[$i]
        }
        { $_ -in '--filter', '-f' } {
            if ($i + 1 -ge $rawArgs.Count) {
                [Console]::Error.WriteLine("Error: $($rawArgs[$i]) needs a value.")
                exit 2
            }
            $i++
            $FilterText = $rawArgs[$i]
        }
        { $_ -in '--yes', '-y' } {
            $AssumeYes = $true
        }
        { $_ -in '--help', '-h' } {
            Write-Host 'Usage:'
            Write-Host '  .\fix-oem.ps1                     interactive menu'
            Write-Host '  .\fix-oem.ps1 --list              list available OEM images'
            Write-Host '  .\fix-oem.ps1 --name NAME         select an image by name'
            Write-Host '  .\fix-oem.ps1 --name NAME --yes   skip the confirmation'
            Write-Host '  .\fix-oem.ps1 --filter TEXT       limit the menu by substring'
            Write-Host '  .\fix-oem.ps1 -s SERIAL           extra arguments go to fastboot'
            exit 0
        }
        default {
            $script:ExtraArgs += $rawArgs[$i]
        }
    }
}

$collectedExtra = @($script:ExtraArgs)

. (Join-Path $PSScriptRoot 'common.ps1')
$script:ExtraArgs = @($collectedExtra)
Read-PackageEnv

Set-Title 'Fix vivo/iQOO OEM identity'

$choices = New-Object System.Collections.Generic.List[object]

function Add-Choice {
    param([string]$Name, [string]$Path, [string]$Kind)
    if ($FilterText) {
        if (-not $Name.ToLowerInvariant().Contains($FilterText.ToLowerInvariant())) {
            return
        }
    }
    $choices.Add([pscustomobject]@{ Name = $Name; Path = $Path; Kind = $Kind })
}

if (Test-Path -LiteralPath $OemDir -PathType Container) {
    foreach ($file in (Get-ChildItem -LiteralPath $OemDir -File -Filter 'OEM_*.img' | Sort-Object Name)) {
        Add-Choice -Name $file.Name.Substring(4) -Path "$OemDir/$($file.Name)" -Kind 'file'
    }
    foreach ($folder in (Get-ChildItem -LiteralPath $OemDir -Directory -Filter 'oem_*' | Sort-Object Name)) {
        Add-Choice -Name $folder.Name -Path "$OemDir/$($folder.Name)" -Kind 'folder'
    }
}

$names = @($choices | ForEach-Object { $_.Name })
$count = $names.Count

if ($ListOnly) {
    if ($count -eq 0) {
        Write-Host "No OEM repair images found in $OemDir."
        exit 0
    }
    foreach ($choice in $choices) {
        if ($choice.Kind -eq 'file') {
            Write-Host $choice.Name
        }
        else {
            Write-Host "folder:$($choice.Name)"
        }
    }
    exit 0
}

if ($count -eq 0) {
    if ($FilterText) {
        Write-Host "No OEM repair image matches filter '$FilterText' in $OemDir."
    }
    else {
        Write-Host "No OEM repair images found in $OemDir."
        Write-Host 'This package was built without OEM variants.'
    }
    if (-not [Console]::IsInputRedirected) { Pause-ForUser }
    exit 0
}

$preferred = Get-PropValue 'ro.vivo.oem.name'
if (-not $preferred) { $preferred = $script:OemIdentity }

$defaultIndex = 0
if ($preferred) {
    for ($i = 0; $i -lt $count; $i++) {
        if ($names[$i].EndsWith('.img')) {
            $candidate = $names[$i].Substring(0, $names[$i].Length - 4)
        }
        else {
            $candidate = $names[$i]
        }
        if ($candidate -eq $preferred) {
            $defaultIndex = $i + 1
            break
        }
    }
}

function Find-Choice {
    param([string[]]$Names, [string]$Query)
    $lowerQuery = $Query.ToLowerInvariant()
    for ($i = 0; $i -lt $Names.Count; $i++) {
        if ($Names[$i].ToLowerInvariant() -eq $lowerQuery) {
            return [pscustomobject]@{ Index = $i + 1; Count = 1 }
        }
    }
    $index = 0
    $matches = 0
    for ($i = 0; $i -lt $Names.Count; $i++) {
        if ($Names[$i].ToLowerInvariant().Contains($lowerQuery)) {
            $index = $i + 1
            $matches++
        }
    }
    return [pscustomobject]@{ Index = $index; Count = $matches }
}

Write-Host ''
Write-Host 'Script notes:'
Write-Host '  -- Restores the OEM/cust identity after a fastboot flash.'
Write-Host '  -- The phone must be unlocked and in Fastboot mode.'
Write-Host '  -- Select by number or by part of the image name.'
Write-Host ''
Write-Host '----------------------------------'
Write-Host ''

for ($i = 0; $i -lt $count; $i++) {
    $marker = ''
    if (($i + 1) -eq $defaultIndex) {
        $marker = '  <- current device identity'
    }
    if ($choices[$i].Kind -eq 'file') {
        Write-Host ("{0,3}. File:   {1}{2}" -f ($i + 1), $names[$i], $marker)
    }
    else {
        Write-Host ("{0,3}. Folder: {1}{2}" -f ($i + 1), $names[$i], $marker)
    }
}
Write-Host ''

if ($SelectName) {
    $match = Find-Choice -Names $names -Query $SelectName
    if ($match.Count -eq 0) {
        [Console]::Error.WriteLine("Error: no OEM image matches '$SelectName'.")
        exit 1
    }
    if ($match.Count -gt 1) {
        [Console]::Error.WriteLine("Error: '$SelectName' matches $($match.Count) images. Be more specific.")
        exit 1
    }
    $choiceNum = $match.Index
}
else {
    if ([Console]::IsInputRedirected) {
        [Console]::Error.WriteLine('Error: no --name given and stdin is not a terminal.')
        [Console]::Error.WriteLine('Use --list and --name NAME for non-interactive use.')
        exit 2
    }
    $choiceNum = 0
    while ($true) {
        if ($defaultIndex -gt 0) {
            $prompt = "Select an option (number or name) [$($names[$defaultIndex - 1])]: "
        }
        else {
            $prompt = 'Select an option (number or name): '
        }
        try {
            $answer = Read-Host $prompt
        }
        catch {
            Write-Host ''
            [Console]::Error.WriteLine('Error: input closed; aborting.')
            exit 2
        }
        if ($null -eq $answer) {
            Write-Host ''
            [Console]::Error.WriteLine('Error: input closed; aborting.')
            exit 2
        }
        $answer = $answer.Trim()
        if ($answer -eq '' -and $defaultIndex -gt 0) {
            $choiceNum = $defaultIndex
            break
        }
        if ($answer -in @('q', 'Q', 'quit', 'exit')) {
            Write-Host 'Canceled.'
            exit 0
        }
        if ($answer -match '^\d+$') {
            $number = [int]$answer
            if ($number -ge 1 -and $number -le $count) {
                $choiceNum = $number
                break
            }
            Write-Host "Invalid number. Enter 1-$count, a name, or q to quit."
            continue
        }
        if ($answer) {
            $match = Find-Choice -Names $names -Query $answer
            if ($match.Count -eq 1) {
                $choiceNum = $match.Index
                break
            }
            elseif ($match.Count -gt 1) {
                Write-Host "'$answer' matches $($match.Count) images. Be more specific."
                continue
            }
        }
        Write-Host 'No match. Enter a number, part of a name, or q to quit.'
    }
}

$selected = $choices[$choiceNum - 1]
$selectedName = $selected.Name
$selectedPath = $selected.Path
$selectedKind = $selected.Kind

Clear-Screen
Write-Host ''
Write-Host "  -- Target: $selectedName"
Write-Host "  -- Path:   $selectedPath"

if ($selectedKind -eq 'file') {
    $baseName = $selectedName.Substring(0, $selectedName.Length - 4)
    $vbmeta = "$OemDir/vbmeta_oem_$baseName.img"
    if (-not (Test-Path -LiteralPath $vbmeta -PathType Leaf)) {
        Die "Missing companion image: $vbmeta"
    }
}

if (-not $AssumeYes) {
    if (-not (Confirm-Action -Prompt 'Flash the selected OEM repair image(s)?' -Default 'n')) {
        Write-Host '-- Canceled.'
        exit 0
    }
}

if (-not (Test-Command 'fastboot')) {
    Die 'fastboot not found in PATH. Install Android platform-tools.'
}

while (-not (Get-FirstFastbootDevice)) {
    Write-Host ''
    Write-Host ' No device connected in Fastboot mode. Waiting...'
    Start-Sleep -Seconds 2
}

$unlock = Get-FastbootResult -Arguments @('getvar', 'unlocked')
if ($unlock.Text -notmatch 'unlocked:\s*yes') {
    Die 'Device is locked. Unlocking the bootloader is required.'
}

Write-Host ''
if ($selectedKind -eq 'file') {
    Flash-Part -Partition 'vbmeta_oem' -Image $vbmeta
    Reboot-Fastboot
    Flash-Part -Partition 'oem' -Image $selectedPath
}
else {
    $slotInfo = Get-FastbootResult -Arguments @('getvar', 'current-slot')
    if ($slotInfo.Text -match 'current-slot:\s*a') {
        $slot = 'a'
    }
    else {
        $slot = 'b'
    }
    Flash-Part -Partition "vbmeta_oem_$slot" -Image "$selectedPath/vbmeta_oem.img"
    Reboot-Fastboot
    Flash-Part -Partition "cust_$slot" -Image "$selectedPath/cust.img"
}

Final-Reboot
Write-Host ''
Write-Host 'Finished.'
Write-Host ''
Pause-ForUser
