[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,50}$')]
    [string]$ExpectedActiveVersion,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,50}$')]
    [string]$RemoveVersion
)

$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $EnvFile).Path
$lines = [Collections.Generic.List[string]]::new()
[IO.File]::ReadAllText($resolved) -split '\r?\n' | ForEach-Object { $lines.Add($_) }

function Get-UniqueValue([string]$Name) {
    $matches = @($lines | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" })
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $Name entry."
    }
    return ($matches[0] -split '=', 2)[1].Trim()
}

$activeVersion = Get-UniqueValue 'NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION'
if ($activeVersion -ne $ExpectedActiveVersion) {
    throw 'The expected active key version does not match the environment file.'
}
if ($RemoveVersion -eq $activeVersion) {
    throw 'The active key version cannot be removed.'
}

$previousRaw = Get-UniqueValue 'NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS'
try {
    $previous = $previousRaw | ConvertFrom-Json -AsHashtable
}
catch {
    throw 'The previous master key configuration is invalid.'
}
if (-not $previous.Contains($RemoveVersion)) {
    throw 'The requested previous key version is not configured.'
}
$previous.Remove($RemoveVersion)
$previousJson = $previous | ConvertTo-Json -Compress
if (-not $previousJson) {
    $previousJson = '{}'
}

for ($index = 0; $index -lt $lines.Count; $index++) {
    if ($lines[$index] -match '^NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS=') {
        $lines[$index] = "NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS=$previousJson"
        break
    }
}

$content = ($lines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine
[IO.File]::WriteAllText($resolved, $content, [Text.UTF8Encoding]::new($false))
Write-Output (
    "Finalized master-key rotation: active_version=$activeVersion " +
    "removed_previous_version=$RemoveVersion."
)
Write-Output 'No key material was displayed.'
