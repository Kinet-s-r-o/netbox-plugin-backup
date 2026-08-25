param(
    [Parameter(Mandatory = $true)]
    [string]$NetBoxDockerPath
)

$root = (Resolve-Path -LiteralPath $NetBoxDockerPath).Path
$envRoot = (Resolve-Path -LiteralPath (Join-Path $root "env")).Path
$targets = @(
    (Join-Path $envRoot "config-backup.env"),
    (Join-Path $envRoot "config-backup-nas.env")
)

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        continue
    }
    $resolved = (Resolve-Path -LiteralPath $target).Path
    if ([System.IO.Path]::GetDirectoryName($resolved) -ne $envRoot) {
        throw "Refusing to change ACL outside the selected NetBox env directory."
    }

    $owner = (Get-Acl -LiteralPath $resolved).Owner
    & icacls.exe $resolved "/inheritance:r" "/grant:r" `
        "${owner}:(F)" "BUILTIN\Administrators:(F)" "NT AUTHORITY\SYSTEM:(F)" `
        "/remove:g" "BUILTIN\Users" "NT AUTHORITY\Authenticated Users" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to protect $resolved."
    }
    Write-Output "Protected $resolved"
}
