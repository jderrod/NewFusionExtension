# Links the add-in into Fusion's AddIns folder (directory junction, no admin needed).
# Fusion then loads it on every start (runOnStartup = true in the manifest).
#   powershell -ExecutionPolicy Bypass -File scripts\install_addin.ps1
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'addin\NewFusionExtension'
$addins = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
$target = Join-Path $addins 'NewFusionExtension'

if ($Uninstall) {
    if (Test-Path $target) {
        # Removing a junction only removes the link, never the project files.
        (Get-Item $target).Delete()
        Write-Host "Removed $target"
    }
    return
}

if (-not (Test-Path $addins)) { New-Item -ItemType Directory -Force $addins | Out-Null }
if (Test-Path $target) {
    $item = Get-Item $target -Force
    if ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') {
        Write-Host "Already linked: $target -> $($item.Target)"
        return
    }
    throw "$target exists and is not a link; move it away first."
}
New-Item -ItemType Junction -Path $target -Target $source | Out-Null
# Pointer used if someone later copies the folder instead of linking it.
Set-Content -Path (Join-Path $source 'nfe_home.txt') -Value $root -Encoding UTF8
Write-Host "Linked $target -> $source"
Write-Host "In Fusion: Utilities > Add-Ins (Shift+S) > Add-Ins tab > NewFusionExtension > Run"
