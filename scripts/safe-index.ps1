#!/usr/bin/env pwsh
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Low-impact index wrapper for Windows development machines.
# Keeps defaults lightweight while delegating behavior to index.py.
#
# Examples:
#   scripts/safe-index.ps1 --quick --path UI
#   scripts/safe-index.ps1 --quick --quick-profile analysis --path Characters --batch-size 5
#   scripts/safe-index.ps1 --all --path /Game/UI --batch-size 10
#   $env:UE_SAFE_MAX_ASSETS=200; scripts/safe-index.ps1 --quick --path /Game/Characters --types Blueprint
#   $env:UE_SAFE_NON_RECURSIVE=1; scripts/safe-index.ps1 --quick --path /Game/UI

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardArgs
)

$repoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoDir

function Get-EnvOrDefault {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Has-Option {
    param([Parameter(Mandatory = $true)][string]$Name)
    foreach ($arg in $ForwardArgs) {
        if ($arg -eq $Name -or $arg.StartsWith("$Name=")) {
            return $true
        }
    }
    return $false
}

$safeBatchSize = Get-EnvOrDefault -Name "UE_SAFE_BATCH_SIZE" -Default "5"
$safeMaxAssets = [Environment]::GetEnvironmentVariable("UE_SAFE_MAX_ASSETS")
$safeNonRecursive = Get-EnvOrDefault -Name "UE_SAFE_NON_RECURSIVE" -Default "0"
$safeParserParallelism = Get-EnvOrDefault -Name "UE_SAFE_PARSER_PARALLELISM" -Default (Get-EnvOrDefault -Name "UE_ASSETPARSER_MAX_PARALLELISM" -Default "2")
$safeBatchTimeout = Get-EnvOrDefault -Name "UE_SAFE_BATCH_TIMEOUT" -Default (Get-EnvOrDefault -Name "UE_INDEX_BATCH_TIMEOUT" -Default "600")
$safeAssetTimeout = Get-EnvOrDefault -Name "UE_SAFE_ASSET_TIMEOUT" -Default (Get-EnvOrDefault -Name "UE_INDEX_ASSET_TIMEOUT" -Default "60")

$cmdArgs = New-Object System.Collections.Generic.List[string]
$cmdArgs.Add("index.py")
foreach ($arg in $ForwardArgs) {
    $cmdArgs.Add($arg)
}

if (-not (Has-Option "--batch-size")) {
    $cmdArgs.Add("--batch-size")
    $cmdArgs.Add($safeBatchSize)
}
if (-not (Has-Option "--timing")) {
    $cmdArgs.Add("--timing")
}
if (-not (Has-Option "--max-assets") -and -not [string]::IsNullOrWhiteSpace($safeMaxAssets)) {
    $cmdArgs.Add("--max-assets")
    $cmdArgs.Add($safeMaxAssets)
}
if (-not (Has-Option "--non-recursive") -and $safeNonRecursive -eq "1") {
    $cmdArgs.Add("--non-recursive")
}
if (-not (Has-Option "--parser-parallelism")) {
    $cmdArgs.Add("--parser-parallelism")
    $cmdArgs.Add($safeParserParallelism)
}
if (-not (Has-Option "--batch-timeout")) {
    $cmdArgs.Add("--batch-timeout")
    $cmdArgs.Add($safeBatchTimeout)
}
if (-not (Has-Option "--asset-timeout")) {
    $cmdArgs.Add("--asset-timeout")
    $cmdArgs.Add($safeAssetTimeout)
}

Write-Host "Running low-impact index command:"
Write-Host "  parser parallelism: $safeParserParallelism"
Write-Host "  batch timeout: $safeBatchTimeout s"
Write-Host "  asset timeout: $safeAssetTimeout s"

$pythonCmd = $null
$pythonArgsPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
    $pythonArgsPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $pythonCmd = "python3"
} else {
    throw "Could not find Python executable (py/python/python3)."
}

$displayParts = @($pythonCmd) + $pythonArgsPrefix + $cmdArgs
Write-Host ("  " + ($displayParts -join " "))

& $pythonCmd @pythonArgsPrefix @cmdArgs
exit $LASTEXITCODE
