# Skyward Sword co-op: start Dolphin with the mod and connect.
#   host:  powershell -ExecutionPolicy Bypass -File play.ps1 host
#   join:  powershell -ExecutionPolicy Bypass -File play.ps1 join 203.0.113.7
# Leave this window open while playing; Ctrl+C (or closing it) disconnects. Closing Dolphin is safe.
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('host', 'join')][string]$Mode,
    [Parameter(Position = 1)][string]$Address,
    [int]$Port = 27645,
    [string]$Bind = "0.0.0.0",           # host only; 127.0.0.1 for a same-PC test without the firewall prompt
    [string]$UserDir = "dolphin-user"
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$user = Join-Path $here $UserDir
$dol = "$here\game\DATA\sys\main.dol"
if (-not (Test-Path $dol) -or -not (Test-Path "$user\GameSettings\SOUE01.ini")) { Write-Host "Run setup.ps1 first." -ForegroundColor Red; exit 1 }
if ($Mode -eq 'join' -and -not $Address) {
    # "3 Join.cmd": ask, offering the address used last time.
    $lastFile = "$here\last-host.txt"
    $last = if (Test-Path $lastFile) { (Get-Content $lastFile -Raw).Trim() } else { "" }
    $prompt = if ($last) { "Host IP address (Enter = $last)" } else { "Host IP address" }
    $Address = (Read-Host $prompt).Trim()
    if (-not $Address) { $Address = $last }
    if (-not $Address) { Write-Host "No address given." -ForegroundColor Red; exit 1 }
    Set-Content -Path $lastFile -Value $Address -Encoding ascii
}

$p = Start-Process "$here\Dolphin-x64\Dolphin.exe" -ArgumentList '-u', "`"$user`"", '-e', "`"$dol`"" -PassThru
Write-Host "Dolphin started (pid $($p.Id)). Waiting for the game..."
& "$here\sscoop.exe" wait $p.Id 180
if ($LASTEXITCODE -ne 0) { Write-Host "The game did not start with the co-op hook. Is EnableCheats on and Dolphin $((Get-Content "$here\mod\manifest.json" -Raw | ConvertFrom-Json).dolphin_tested)?" -ForegroundColor Red; exit 1 }

if ($Mode -eq 'host') {
    Write-Host "Hosting on UDP port $Port. Your friend runs: play.ps1 join <your public IP>" -ForegroundColor Green
    & "$here\sscoop.exe" bridge --pid $p.Id host --bind $Bind --port $Port
} else {
    $target = if ($Address -match ':\d+$') { $Address } else { "${Address}:$Port" }
    Write-Host "Joining $target" -ForegroundColor Green
    & "$here\sscoop.exe" bridge --pid $p.Id join $target
}
