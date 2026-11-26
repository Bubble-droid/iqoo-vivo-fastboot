# Re-sign the AVB images of this package after local modifications.
#
# The package was built with a custom AVB key.  If an image is changed
# afterwards (for example, boot.img patched by Magisk or KernelSU), its
# vbmeta footer no longer matches the content.  This script removes the old
# footer, recomputes the hash or dm-verity hashtree, and signs the result
# again with the same private key.
#
# The private key is never stored in the package.  Pass it with --key, set
# AVB_KEY, or place avb.pem next to this script.
#
# Usage:
#   .\resign.ps1 boot
#   .\resign.ps1 --all
#   .\resign.ps1 -k C:\keys\avb.pem firmware-update\boot.img
#
# See resign.sh for the full option list; both scripts behave the same.

$ErrorActionPreference = 'Stop'
$script:ScriptDir = $PSScriptRoot
if (-not $script:ScriptDir) {
    $script:ScriptDir = (Get-Location).Path
}
Set-Location -LiteralPath $script:ScriptDir

$script:DefaultSalt = 'aee087a5be3b982978c923f566a94613496b417f2af592639bc80d141e34dfe7'
$script:DefaultAlgorithm = 'SHA256_RSA2048'
$script:DefaultHash = 'sha256'
$script:AllImages = @('boot', 'dtbo', 'vendor_boot', 'vbmeta_system', 'vbmeta_vgc', 'vbmeta')

$script:Key = if ($env:AVB_KEY) { $env:AVB_KEY } else { '' }
$script:Targets = New-Object System.Collections.Generic.List[string]
$script:All = $false
$script:PartitionSize = ''
$script:Salt = ''
$script:HashAlgorithm = ''
$script:Algorithm = ''
$script:Force = $false
$script:Verify = $true
$script:DryRun = $false
$script:Backup = $false

function Write-Note {
    param([string]$Message)
    Write-Host $Message
}

function Die {
    param([string]$Message)
    [Console]::Error.WriteLine("-- $Message")
    exit 1
}

function Show-Usage {
    @'
Re-sign the AVB images of this package after local modifications.

Usage:
  .\resign.ps1 [options] IMAGE...

IMAGE is a partition name (boot), a file name (boot.img), or a path.

Options:
  -k, --key PATH          AVB private key (default: AVB_KEY, .\avb.pem,
                          .\avb\avb.pem, ~\.config\iqoo-vivo-fastboot\avb.pem)
  -a, --all               boot, dtbo, vendor_boot, vbmeta_system,
                          vbmeta_vgc and vbmeta, when present
      --partition-size N  partition size in bytes (single image only)
      --salt HEX          override the hash salt
      --hash-algorithm A  override sha256
      --algorithm A       override SHA256_RSA2048
      --force             allow a key mismatch, a missing footer, or a
                          boot header v4 signature that cannot be updated
      --backup            keep a .bak copy before modifying an image
      --no-verify         skip the avbtool verify_image check
  -n, --dry-run           print the commands without changing anything
  -h, --help              show this help

Supported images:
  boot, dtbo, vendor_boot       hash footer, recomputed
  vbmeta, vbmeta_system, vbmeta_vgc
                                standalone vbmeta, re-signed in place
  system, system_ext, product, vendor, vendor_dlkm, odm
                                hashtree footer, recomputed; rebuild
                                vbmeta_system afterwards

The top-level vbmeta chain stores public keys, not image digests, so it
stays valid as long as the same key is used.  A different key requires a
full rebuild with ota2fastboot.
'@ | Write-Host
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-Sha1 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA1).Hash.ToLowerInvariant()
}

function Invoke-Avbtool {
    param([string[]]$Arguments)
    if ($script:DryRun) {
        $quoted = @($Arguments | ForEach-Object {
                if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
            })
        Write-Host ("  would run: avbtool " + ($quoted -join ' '))
        return $true
    }
    $output = & avbtool @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($output) {
        $output | ForEach-Object { Write-Host "$_" }
    }
    return ($code -eq 0)
}

function Get-AvbInfo {
    param([string]$Path)
    $output = & avbtool info_image --image $Path 2>&1
    if ($LASTEXITCODE -ne 0) { return '' }
    return (($output | ForEach-Object { "$_" }) -join "`n")
}

function Get-AvbField {
    param([string]$Text, [string]$Key, [string]$Section = '')
    $current = ''
    $pattern = '^\s*' + [regex]::Escape($Key) + ':\s*(.*)$'
    foreach ($line in ($Text -split "`n")) {
        if ($line -match '^\s+Hash descriptor:') { $current = 'hash'; continue }
        if ($line -match '^\s+Hashtree descriptor:') { $current = 'hashtree'; continue }
        if ($line -match '^\s+Chain Partition descriptor:') { $current = 'chain'; continue }
        if ($line -match '^\s+Prop:') { $current = 'prop'; continue }
        if ($line -match '^\S') { $current = '' }
        if ($line -match $pattern) {
            if ($Section -eq '' -or $current -eq $Section) {
                return $Matches[1].Trim()
            }
        }
    }
    return ''
}

function Get-AvbProps {
    param([string]$Text)
    $props = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($Text -split "`n")) {
        if ($line -match "^\s*Prop: ([^ ]+) -> '(.*)'\s*$") {
            $props.Add($Matches[1] + ':' + $Matches[2])
        }
    }
    return $props.ToArray()
}

function Resolve-Image {
    param([string]$Arg)
    if (Test-Path -LiteralPath $Arg -PathType Leaf) { return $Arg }
    $candidate = Join-Path 'firmware-update' $Arg
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    $candidate = Join-Path 'firmware-update' ($Arg + '.img')
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return ''
}

function Set-FileLength {
    param([string]$Path, [long]$Length)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
    try { $stream.SetLength($Length) } finally { $stream.Close() }
}

function Get-Le32 {
    param([string]$Path, [int]$Offset)
    $stream = [IO.File]::OpenRead($Path)
    try {
        $stream.Seek($Offset, [IO.SeekOrigin]::Begin) | Out-Null
        $buffer = New-Object byte[] 4
        $null = $stream.Read($buffer, 0, 4)
        return [BitConverter]::ToUInt32($buffer, 0)
    }
    finally { $stream.Close() }
}

function Test-BootMagic {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    try {
        $buffer = New-Object byte[] 8
        $null = $stream.Read($buffer, 0, 8)
        return ([Text.Encoding]::ASCII.GetString($buffer) -eq 'ANDROID!')
    }
    finally { $stream.Close() }
}

$raw = @($args)
$i = 0
while ($i -lt $raw.Count) {
    $arg = "$($raw[$i])"
    if ($arg -eq '--') {
        $i++
        while ($i -lt $raw.Count) { $script:Targets.Add("$($raw[$i])"); $i++ }
        continue
    }
    if ($arg -eq '-k' -or $arg -eq '--key') {
        if ($i + 1 -ge $raw.Count) { Die "$arg needs a path" }
        $script:Key = "$($raw[$i + 1])"
        $i += 2
        continue
    }
    if ($arg -match '^--key=(.*)$') { $script:Key = $Matches[1]; $i++; continue }
    if ($arg -eq '-a' -or $arg -eq '--all') { $script:All = $true; $i++; continue }
    if ($arg -eq '--partition-size') {
        if ($i + 1 -ge $raw.Count) { Die "$arg needs a size" }
        $script:PartitionSize = "$($raw[$i + 1])"
        $i += 2
        continue
    }
    if ($arg -match '^--partition-size=(.*)$') { $script:PartitionSize = $Matches[1]; $i++; continue }
    if ($arg -eq '--salt') {
        if ($i + 1 -ge $raw.Count) { Die "$arg needs a hex value" }
        $script:Salt = "$($raw[$i + 1])"
        $i += 2
        continue
    }
    if ($arg -match '^--salt=(.*)$') { $script:Salt = $Matches[1]; $i++; continue }
    if ($arg -eq '--hash-algorithm') {
        if ($i + 1 -ge $raw.Count) { Die "$arg needs a value" }
        $script:HashAlgorithm = "$($raw[$i + 1])"
        $i += 2
        continue
    }
    if ($arg -match '^--hash-algorithm=(.*)$') { $script:HashAlgorithm = $Matches[1]; $i++; continue }
    if ($arg -eq '--algorithm') {
        if ($i + 1 -ge $raw.Count) { Die "$arg needs a value" }
        $script:Algorithm = "$($raw[$i + 1])"
        $i += 2
        continue
    }
    if ($arg -match '^--algorithm=(.*)$') { $script:Algorithm = $Matches[1]; $i++; continue }
    if ($arg -eq '--force') { $script:Force = $true; $i++; continue }
    if ($arg -eq '--no-verify') { $script:Verify = $false; $i++; continue }
    if ($arg -eq '--dry-run' -or $arg -eq '-n') { $script:DryRun = $true; $i++; continue }
    if ($arg -eq '--backup') { $script:Backup = $true; $i++; continue }
    if ($arg -eq '-h' -or $arg -eq '--help') { Show-Usage; exit 0 }
    if ($arg.StartsWith('-')) { Die "unknown option: $arg (try --help)" }
    $script:Targets.Add($arg)
    $i++
}

if (-not (Test-Command 'avbtool')) {
    Die 'avbtool not found in PATH. Install android-sdk-libsparse-utils.'
}

if (-not $script:Key) {
    $candidates = @(
        (Join-Path $script:ScriptDir 'avb.pem'),
        (Join-Path $script:ScriptDir 'avb\avb.pem')
    )
    if ($HOME) {
        $candidates += (Join-Path $HOME '.config\iqoo-vivo-fastboot\avb.pem')
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $script:Key = $candidate
            break
        }
    }
}

if (-not $script:Key) {
    Die 'no AVB private key. Pass --key PATH, set AVB_KEY, or put avb.pem next to this script.'
}
if (-not (Test-Path -LiteralPath $script:Key -PathType Leaf)) {
    Die "AVB private key not found: $script:Key"
}

if ($script:All) {
    foreach ($name in $script:AllImages) {
        $candidate = Join-Path 'firmware-update' ($name + '.img')
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $script:Targets.Add($name)
        }
    }
}

if ($script:Targets.Count -eq 0) {
    Die 'no images specified. Use .\resign.ps1 boot, .\resign.ps1 --all, or pass an image path.'
}
if ($script:PartitionSize -and $script:Targets.Count -ne 1) {
    Die '--partition-size can only be used with a single image'
}

$tmpDir = Join-Path ([IO.Path]::GetTempPath()) ('resign-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpDir | Out-Null
$tmpPub = Join-Path $tmpDir 'custom.avbpubkey'
& avbtool extract_public_key --key $script:Key --output $tmpPub | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $tmpDir -Recurse -Force
    Die "cannot read the AVB private key: $script:Key"
}
$keySha1 = Get-Sha1 $tmpPub
$packagePub = Join-Path $script:ScriptDir 'avb\custom.avbpubkey'
if (Test-Path -LiteralPath $packagePub -PathType Leaf) {
    $packageSha1 = Get-Sha1 $packagePub
    if ($keySha1 -ne $packageSha1) {
        if (-not $script:Force) {
            Remove-Item -LiteralPath $tmpDir -Recurse -Force
            Die "the key ($keySha1) does not match this package's AVB key ($packageSha1). Use the key passed to --avb-key at build time, or rebuild the whole chain with a new key. Pass --force to sign anyway."
        }
        Write-Note '-- warning: signing with a key that does not match avb\custom.avbpubkey'
    }
}
Remove-Item -LiteralPath $tmpDir -Recurse -Force

Write-Note "-- key: $script:Key (sha1 $keySha1)"

$signed = 0
$hashtreeSigned = 0

foreach ($target in $script:Targets) {
    $path = Resolve-Image $target
    if (-not $path) {
        Die "image not found: $target (tried the path, firmware-update\$target and firmware-update\$target.img)"
    }

    Write-Note ''
    Write-Note "-- $path"
    $isVbmeta = $false

    if ($script:Backup) {
        if ($script:DryRun) {
            Write-Note "  would back up to $path.bak"
        }
        elseif (Test-Path -LiteralPath ($path + '.bak')) {
            Write-Note "  keeping existing backup: $path.bak"
        }
        else {
            Copy-Item -LiteralPath $path -Destination ($path + '.bak')
            Write-Note "  backup: $path.bak"
        }
    }

    if (Test-BootMagic $path) {
        $headerVersion = Get-Le32 $path 40
        if ($headerVersion -ge 4) {
            $bootSignature = Get-Le32 $path 1580
            if ($bootSignature -gt 0) {
                Write-Note "  warning: boot header v4 signature is present ($bootSignature bytes); only the AVB footer is updated"
                if (-not $script:Force) {
                    Die 'this script cannot recompute the boot header signature. Pass --force if the patcher already updated it, or rebuild boot with the vendor tools.'
                }
            }
        }
    }

    $info = Get-AvbInfo $path
    if (-not $info) {
        if (-not $script:Force) {
            Die "$path has no AVB footer. Patch the image with AVB preservation enabled, or pass --force with --partition-size to add a new footer."
        }
        $name = [IO.Path]::GetFileNameWithoutExtension($path)
        $currentSize = (Get-Item -LiteralPath $path).Length
        $partitionSize = if ($script:PartitionSize) { [long]$script:PartitionSize } else { $currentSize }
        if ($partitionSize -le $currentSize) {
            Die "$path is not padded to the partition size; pass --partition-size (for example 100663296 for boot)."
        }
        $salt = if ($script:Salt) { $script:Salt } else { $script:DefaultSalt }
        $hashAlg = if ($script:HashAlgorithm) { $script:HashAlgorithm } else { $script:DefaultHash }
        $algorithm = if ($script:Algorithm) { $script:Algorithm } else { $script:DefaultAlgorithm }
        Write-Note "  no footer; adding a new hash footer for $name ($partitionSize bytes)"
        $arguments = @(
            'add_hash_footer', '--image', $path, '--partition_name', $name,
            '--partition_size', "$partitionSize", '--hash_algorithm', $hashAlg,
            '--salt', $salt, '--key', $script:Key, '--algorithm', $algorithm
        )
        if (-not (Invoke-Avbtool $arguments)) { Die "could not sign $path" }
        if ($script:Verify -and -not $script:DryRun) {
            & avbtool verify_image --image $path | Out-Null
            if ($LASTEXITCODE -ne 0) { Die "verification failed for $path" }
        }
        $signed++
        continue
    }

    $currentKey = Get-AvbField $info 'Public key (sha1)'
    if ($currentKey -and $currentKey -ne $keySha1) {
        Write-Note "  note: the image is currently signed with key $currentKey"
    }

    if ($info -match '(?m)^Footer version:') {
        if ($info -match '(?m)^\s*Hash descriptor:') {
            $name = Get-AvbField $info 'Partition Name' 'hash'
            $salt = Get-AvbField $info 'Salt' 'hash'
            $hashAlg = Get-AvbField $info 'Hash Algorithm' 'hash'
            $flags = Get-AvbField $info 'Flags'
            $rollback = Get-AvbField $info 'Rollback Index'
            $rollbackLoc = Get-AvbField $info 'Rollback Index Location'
            $algorithm = Get-AvbField $info 'Algorithm'
            $cutText = Get-AvbField $info 'VBMeta offset'
            if (-not $cutText) { Die "${path}: no VBMeta offset in the footer" }
            $cut = [long](($cutText -split '\s+')[0])

            if (-not $name) { Die "${path}: no partition name in the hash descriptor" }
            if ($cut -le 0) { Die "${path}: no VBMeta offset in the footer" }
            $salt = if ($script:Salt) { $script:Salt } elseif ($salt) { $salt } else { $script:DefaultSalt }
            $hashAlg = if ($script:HashAlgorithm) { $script:HashAlgorithm } elseif ($hashAlg) { $hashAlg } else { $script:DefaultHash }
            $algorithm = if ($script:Algorithm) { $script:Algorithm } elseif ($algorithm) { $algorithm } else { $script:DefaultAlgorithm }
            if ($algorithm -eq 'NONE') { $algorithm = $script:DefaultAlgorithm }
            if (-not $flags) { $flags = '0' }
            if (-not $rollback) { $rollback = '0' }
            if (-not $rollbackLoc) { $rollbackLoc = '0' }
            $partitionSize = if ($script:PartitionSize) { [long]$script:PartitionSize } else { (Get-Item -LiteralPath $path).Length }

            Write-Note "  hash footer: partition=$name size=$partitionSize content=$cut"
            if ($partitionSize -lt $cut) {
                Die "${path}: partition size ($partitionSize) is smaller than the content ($cut)"
            }
            if ($cut -gt (Get-Item -LiteralPath $path).Length) {
                Die "${path}: the footer points past the end of the file; re-extract the image"
            }
            if ($script:DryRun) { Write-Note "  would truncate to $cut bytes" }
            else { Set-FileLength $path $cut }

            $arguments = @(
                'add_hash_footer', '--image', $path, '--partition_name', $name,
                '--partition_size', "$partitionSize", '--hash_algorithm', $hashAlg,
                '--salt', $salt, '--key', $script:Key, '--algorithm', $algorithm,
                '--flags', $flags, '--rollback_index', $rollback,
                '--rollback_index_location', $rollbackLoc
            )
            foreach ($prop in (Get-AvbProps $info)) {
                if ($prop) { $arguments += @('--prop', $prop) }
            }
            if (-not (Invoke-Avbtool $arguments)) { Die "could not sign $path" }
            $signed++
        }
        elseif ($info -match '(?m)^\s*Hashtree descriptor:') {
            $name = Get-AvbField $info 'Partition Name' 'hashtree'
            $salt = Get-AvbField $info 'Salt' 'hashtree'
            $hashAlg = Get-AvbField $info 'Hash Algorithm' 'hashtree'
            $blockSize = Get-AvbField $info 'Data Block Size' 'hashtree'
            $fecRoots = Get-AvbField $info 'FEC num roots' 'hashtree'
            $flags = Get-AvbField $info 'Flags'
            $rollback = Get-AvbField $info 'Rollback Index'
            $rollbackLoc = Get-AvbField $info 'Rollback Index Location'
            $algorithm = Get-AvbField $info 'Algorithm'
            $cutText = Get-AvbField $info 'Tree Offset' 'hashtree'
            if (-not $cutText) { Die "${path}: no tree offset in the hashtree descriptor" }
            $cut = [long](($cutText -split '\s+')[0])
            $blockSize = ($blockSize -split '\s+')[0]

            if (-not $name) { Die "${path}: no partition name in the hashtree descriptor" }
            if ($cut -le 0) { Die "${path}: no tree offset in the hashtree descriptor" }
            $salt = if ($script:Salt) { $script:Salt } elseif ($salt) { $salt } else { $script:DefaultSalt }
            $hashAlg = if ($script:HashAlgorithm) { $script:HashAlgorithm } elseif ($hashAlg) { $hashAlg } else { $script:DefaultHash }
            $algorithm = if ($script:Algorithm) { $script:Algorithm } elseif ($algorithm) { $algorithm } else { $script:DefaultAlgorithm }
            if ($algorithm -eq 'NONE') { $algorithm = $script:DefaultAlgorithm }
            if (-not $blockSize) { $blockSize = '4096' }
            if (-not $flags) { $flags = '0' }
            if (-not $rollback) { $rollback = '0' }
            if (-not $rollbackLoc) { $rollbackLoc = '0' }
            $partitionSize = if ($script:PartitionSize) { [long]$script:PartitionSize } else { (Get-Item -LiteralPath $path).Length }

            Write-Note "  hashtree footer: partition=$name size=$partitionSize content=$cut block=$blockSize fec=$fecRoots"
            if ($partitionSize -lt $cut) {
                Die "${path}: partition size ($partitionSize) is smaller than the content ($cut)"
            }
            if ($cut -gt (Get-Item -LiteralPath $path).Length) {
                Die "${path}: the tree offset points past the end of the file; re-extract the image"
            }
            if ($script:DryRun) { Write-Note "  would truncate to $cut bytes" }
            else { Set-FileLength $path $cut }

            $arguments = @(
                'add_hashtree_footer', '--image', $path, '--partition_name', $name,
                '--partition_size', "$partitionSize", '--hash_algorithm', $hashAlg,
                '--salt', $salt, '--block_size', $blockSize, '--key', $script:Key,
                '--algorithm', $algorithm, '--flags', $flags,
                '--rollback_index', $rollback,
                '--rollback_index_location', $rollbackLoc
            )
            $usedFec = $false
            if ($fecRoots -match '^\d+$' -and [int]$fecRoots -gt 0) {
                if (Test-Command 'fec') {
                    $arguments += @('--fec_num_roots', $fecRoots)
                    $usedFec = $true
                }
                else {
                    Write-Note '  note: the fec tool is not installed; FEC will be omitted'
                    $arguments += '--do_not_generate_fec'
                }
            }
            else {
                $arguments += '--do_not_generate_fec'
            }

            if (-not (Invoke-Avbtool $arguments)) {
                if ($usedFec) {
                    Write-Note '  warning: the image does not fit with FEC; retrying without it'
                    $fixed = @()
                    $index = 0
                    while ($index -lt $arguments.Count) {
                        if ($arguments[$index] -eq '--fec_num_roots') {
                            $index += 2
                            continue
                        }
                        $fixed += $arguments[$index]
                        $index++
                    }
                    $fixed += '--do_not_generate_fec'
                    if (-not (Invoke-Avbtool $fixed)) { Die "could not add a hashtree footer to $path" }
                }
                else {
                    Die "could not add a hashtree footer to $path"
                }
            }
            $signed++
            $hashtreeSigned++
        }
        else {
            Die "${path}: unsupported AVB footer (neither hash nor hashtree)"
        }
    }
    else {
        $algorithm = Get-AvbField $info 'Algorithm'
        $algorithm = if ($script:Algorithm) { $script:Algorithm } elseif ($algorithm) { $algorithm } else { $script:DefaultAlgorithm }
        if ($algorithm -eq 'NONE') {
            Write-Note '  unsigned vbmeta; left untouched'
            continue
        }
        Write-Note '  standalone vbmeta: re-signing in place'
        if (-not (Invoke-Avbtool @('resign_image', '--image', $path, '--key', $script:Key, '--algorithm', $algorithm))) {
            Die "could not re-sign $path"
        }
        $signed++
        $isVbmeta = $true
    }

    if ($script:Verify -and -not $script:DryRun) {
        if ($isVbmeta) {
            Write-Note '  signature rewritten; full chain verification needs the logical partition images'
        }
        else {
            & avbtool verify_image --image $path | Out-Null
            if ($LASTEXITCODE -ne 0) {
                & avbtool verify_image --image $path
                Die "verification failed for $path"
            }
            Write-Note '  verified'
        }
    }
}

Write-Note ''
Write-Note "-- re-signed $signed image(s)."
if ($hashtreeSigned -gt 0) {
    Write-Note '-- note: hashtree descriptors live in vbmeta_system. Rebuild it with ota2fastboot (or pass the updated images to it) so the new root digests are included.'
}
Write-Note '-- the top-level vbmeta chain was not modified; it stays valid only with the build key.'
