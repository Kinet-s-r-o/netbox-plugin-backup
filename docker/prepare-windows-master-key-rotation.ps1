[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,50}$')]
    [string]$NewVersion
)

$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $EnvFile).Path
$raw = [IO.File]::ReadAllText($resolved)
if ($raw.Length -gt 65536) {
    throw 'The environment file is unexpectedly large.'
}

$lines = [Collections.Generic.List[string]]::new()
$raw -split '\r?\n' | ForEach-Object { $lines.Add($_) }

function Get-UniqueValue([string]$Name) {
    $matches = @($lines | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" })
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $Name entry."
    }
    return ($matches[0] -split '=', 2)[1].Trim()
}

function Assert-Key([string]$Encoded) {
    try {
        $padded = $Encoded.Replace('-', '+').Replace('_', '/')
        $padded += '=' * ((4 - ($padded.Length % 4)) % 4)
        $bytes = [Convert]::FromBase64String($padded)
    }
    catch {
        throw 'A configured master key is not valid base64url.'
    }
    if ($bytes.Length -ne 32) {
        throw 'A configured master key does not decode to 32 bytes.'
    }
}

$oldKey = Get-UniqueValue 'NETBOX_CONFIG_BACKUP_MASTER_KEY'
$oldVersion = Get-UniqueValue 'NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION'
if ($oldVersion -notmatch '^[A-Za-z0-9_.-]{1,50}$') {
    throw 'The active master key version is invalid.'
}
if ($NewVersion -eq $oldVersion) {
    throw 'The new key version must differ from the active version.'
}
Assert-Key $oldKey

$previousLine = @(
    $lines | Where-Object { $_ -match '^NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS=(.*)$' }
)
if ($previousLine.Count -gt 1) {
    throw 'The previous master key setting is duplicated.'
}
$previous = [ordered]@{}
if ($previousLine.Count -eq 1) {
    $previousRaw = ($previousLine[0] -split '=', 2)[1].Trim()
    if ($previousRaw -and $previousRaw -ne '{}') {
        $parsed = $previousRaw | ConvertFrom-Json -AsHashtable
        foreach ($entry in $parsed.GetEnumerator()) {
            Assert-Key ([string]$entry.Value)
            $previous[[string]$entry.Key] = [string]$entry.Value
        }
    }
}
if ($previous.Contains($NewVersion)) {
    throw 'The new key version is already present in the previous-key keyring.'
}
$previous[$oldVersion] = $oldKey

$newBytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($newBytes)
$newKey = [Convert]::ToBase64String($newBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$previousJson = $previous | ConvertTo-Json -Compress

function Set-Value([string]$Name, [string]$Value) {
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^$([regex]::Escape($Name))=") {
            $lines[$index] = "$Name=$Value"
            return
        }
    }
    $lines.Add("$Name=$Value")
}

Set-Value 'NETBOX_CONFIG_BACKUP_MASTER_KEY' $newKey
Set-Value 'NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION' $NewVersion
Set-Value 'NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS' $previousJson

$content = ($lines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine
[IO.File]::WriteAllText($resolved, $content, [Text.UTF8Encoding]::new($false))
Write-Output "Prepared master-key rotation: previous_version=$oldVersion active_version=$NewVersion."
Write-Output 'No key material was displayed.'
