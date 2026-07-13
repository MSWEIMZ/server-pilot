[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$CanonicalRoot = "C:\Users\WEI\server-pilot",
    [string]$UserProfile = $env:USERPROFILE
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-PathWithin {
    param([string]$Path, [string]$Root)
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\\')
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\\')
    return ($fullPath.StartsWith($fullRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.Equals($fullRoot, [StringComparison]::OrdinalIgnoreCase))
}

function Get-ManagedTargets {
    param([string]$Profile)
    @(
        (Join-Path $Profile '.codex\skills\my-server-ssh'),
        (Join-Path $Profile '.claude\skills\my-server-ssh'),
        (Join-Path $Profile '.cc-switch\skills\my-server-ssh'),
        (Join-Path $Profile '.qoderworkcn\skills\server-pilot'),
        (Join-Path $Profile '.codebuddy\skills\server-pilot'),
        (Join-Path $Profile '.zcode\skills\my-server-ssh')
    )
}

function Get-MigrationPlan {
    param([string]$CanonicalRoot, [string]$UserProfile)
    if (-not (Test-PathWithin $CanonicalRoot $UserProfile)) {
        throw "CanonicalRoot must stay inside the user profile."
    }
    Get-ManagedTargets $UserProfile | ForEach-Object {
        if (-not (Test-PathWithin $_ $UserProfile)) {
            throw "Managed target escapes the user profile: $_"
        }
        [PSCustomObject]@{ Target = $_; Exists = Test-Path -LiteralPath $_; IsCanonical = $_.Equals($CanonicalRoot, [StringComparison]::OrdinalIgnoreCase) }
    } | Where-Object { -not $_.IsCanonical }
}

function Invoke-Migration {
    param([string]$CanonicalRoot, [string]$UserProfile)
    if (-not (Test-Path -LiteralPath $CanonicalRoot)) { throw "Canonical root does not exist: $CanonicalRoot" }
    $plan = @(Get-MigrationPlan -CanonicalRoot $CanonicalRoot -UserProfile $UserProfile)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupRoot = Join-Path $CanonicalRoot ("backups\\" + $stamp)
    $configSource = Join-Path $UserProfile '.codex\skills\my-server-ssh\scripts\server_config.json'
    $configTarget = Join-Path $CanonicalRoot 'scripts\server_config.json'
    if (-not (Test-Path -LiteralPath $configSource)) { throw "The selected canonical configuration is missing." }

    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    foreach ($item in $plan) {
        if (-not $item.Exists) { continue }
        $safeName = ($item.Target.Substring($UserProfile.Length).TrimStart('\\') -replace '[\\/:*?"<>|]', '_')
        Copy-Item -LiteralPath $item.Target -Destination (Join-Path $backupRoot $safeName) -Recurse -Force
    }
    if (Test-Path -LiteralPath $configTarget) {
        Copy-Item -LiteralPath $configTarget -Destination (Join-Path $backupRoot 'source-server_config.json') -Force
    }
    Copy-Item -LiteralPath $configSource -Destination $configTarget -Force

    foreach ($item in $plan) {
        if (-not $item.Exists) { continue }
        $archived = $item.Target + '.pre-canonical-' + $stamp
        Move-Item -LiteralPath $item.Target -Destination $archived
        New-Item -ItemType Junction -Path $item.Target -Target $CanonicalRoot | Out-Null
    }
    return $backupRoot
}

if ($MyInvocation.InvocationName -ne '.') {
    $plan = @(Get-MigrationPlan -CanonicalRoot $CanonicalRoot -UserProfile $UserProfile)
    if (-not $Apply) {
        $plan | Format-Table Target, Exists -AutoSize
        Write-Host 'Dry run only. Re-run with -Apply to back up and create junctions.'
        exit 0
    }
    $backup = Invoke-Migration -CanonicalRoot $CanonicalRoot -UserProfile $UserProfile
    Write-Host "Migration complete. Backups: $backup"
}
