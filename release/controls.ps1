# Xbox controller setup for the co-op Dolphin (dolphin-user\), so no Wii Remote or Nunchuk is needed.
#   controls.ps1            put the profile into Dolphin's profile list; use it only if no controls are set up yet
#   controls.ps1 -Apply     use it now (the current controls are kept as WiimoteNew.ini.backup-<date>)
# Layout: see README.md, "Controls". Works with any Xbox controller on Windows (Dolphin's XInput device).
param([switch]$Apply, [string]$UserDir = "dolphin-user")
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$user = Join-Path $here $UserDir
$name = "Xbox controller (ss-coop)"
$profile = Join-Path $here "controls\$name.ini"

$profDir = "$user\Config\Profiles\Wiimote"
New-Item -ItemType Directory -Force $profDir | Out-Null
Copy-Item $profile "$profDir\$name.ini" -Force

$ini = "$user\Config\WiimoteNew.ini"
if ((Test-Path $ini) -and -not $Apply) {
    Write-Host "Controls already set up: kept. The Xbox layout is in Dolphin's Controllers window under Profile '$name'."
    return
}
if (Test-Path $ini) {
    $bak = "$ini.backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $ini $bak
    Write-Host "Previous controls kept in $bak"
}
# WiimoteNew.ini = the profile as Wii Remote 1, the other slots unused.
$lines = @('[Wiimote1]') + (Get-Content $profile | Select-Object -Skip 1)
foreach ($n in 2..4) { $lines += "[Wiimote$n]", 'Device = DInput/0/Keyboard Mouse' }
$lines += '[BalanceBoard]', 'Device = DInput/0/Keyboard Mouse'
New-Item -ItemType Directory -Force "$user\Config" | Out-Null
[IO.File]::WriteAllLines($ini, $lines)
Write-Host "Xbox controller layout set for Wii Remote 1 (with Nunchuk)." -ForegroundColor Green
