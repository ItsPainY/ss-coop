# Skyward Sword co-op, one-time setup. Uses YOUR OWN disc dump; nothing of the game ships with this mod.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Disc "C:\path\to\Skyward Sword.rvz" [-Save "...\wiiking2.sav"]
# Needs: Dolphin 2609a unzipped into .\Dolphin-x64. No Python: helpers are in sscoop.exe (source in bridge\, tools\).
param(
    [string]$Disc,                       # asked with a file dialog when missing ("1 Setup.cmd")
    [string]$Save,                       # optional save folder; .\host-save is used automatically if present
    [string]$UserDir = "dolphin-user"    # Dolphin settings folder for co-op (relative to this folder)
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
# Update mode: the game was extracted by an earlier setup (its original rels.arc is kept), so no disc is needed;
# main.dol and rels.arc.original are still checked against the manifest below.
$update = (-not $Disc) -and (Test-Path "$here\game\DATA\sys\main.dol") -and (Test-Path "$here\game\rels.arc.original")
if ($update) { Write-Host "Game already set up: updating the mod files only." }
if (-not $Disc -and -not $update) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Title = "Choose your Skyward Sword disc dump (Wii, USA)"
    $dlg.Filter = "Wii disc images (*.rvz;*.iso;*.wbfs;*.gcz;*.wia)|*.rvz;*.iso;*.wbfs;*.gcz;*.wia|All files (*.*)|*.*"
    if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host "No disc chosen, setup cancelled."; exit 1 }
    $Disc = $dlg.FileName
}
# host-save\ is only picked up automatically for a first setup: running setup again must not replace newer progress.
$haveSave = Test-Path (Join-Path (Join-Path $here $UserDir) "Wii\title\00010000\534f5545\data\wiiking2.sav")
if (-not $Save -and -not $haveSave -and (Test-Path "$here\host-save\wiiking2.sav")) {
    $Save = "$here\host-save"
    Write-Host "Using the save in host-save\"
}
$man = Get-Content "$here\mod\manifest.json" -Raw | ConvertFrom-Json
$tool = "$here\Dolphin-x64\DolphinTool.exe"
$game = "$here\game"
$user = Join-Path $here $UserDir
function Fail($msg) { Write-Host "SETUP FAILED: $msg" -ForegroundColor Red; exit 1 }
function Sha($path, $alg) { (Get-FileHash $path -Algorithm $alg).Hash.ToLower() }

if (-not (Test-Path $tool)) { Fail "Dolphin not found. Unzip Dolphin $($man.dolphin_tested) so that $tool exists." }
$exe = "$here\sscoop.exe"   # the Python helpers, bundled: no Python needed
if (-not (Test-Path $exe)) { Fail "sscoop.exe is missing from this folder (unzip the whole package)." }
if (-not $update) {
    if (-not (Test-Path $Disc)) { Fail "Disc dump not found: $Disc" }

    # 1. Right game and version?
    $ErrorActionPreference = 'Continue'
    $hdr = & $tool header -i $Disc 2>&1 | Out-String
    $ErrorActionPreference = 'Stop'
    if ($hdr -notmatch "Game ID: $($man.game_id)") { Fail "this is not $($man.game_id) (USA). DolphinTool says:`n$hdr" }
    if ($hdr -notmatch "Revision: $($man.revision)\b") { Fail "wrong revision (need $($man.revision)). DolphinTool says:`n$hdr" }
    Write-Host "Disc OK: $($man.game_id) revision $($man.revision)"
}

# 2. Extract the game data once (about 4.7 GB).
$dol = "$game\DATA\sys\main.dol"
$arc = "$game\DATA\files\rels.arc"
$orig = "$game\rels.arc.original"
if (-not (Test-Path $dol)) {
    Write-Host "Extracting the game (a few minutes)..."
    & $tool extract -i $Disc -o $game -g -q
    if (-not (Test-Path $dol)) { Fail "extraction did not produce $dol" }
}
if ((Sha $dol SHA1) -ne $man.main_dol_sha1) { Fail "main.dol does not match the version this mod was built for" }

# 3. Swap the co-op puppet module into rels.arc (original kept as rels.arc.original).
if (-not (Test-Path $orig)) {
    if ((Sha $arc SHA256) -ne $man.rels_arc_original_sha256) { Fail "rels.arc is not the original; delete the game folder and run setup again" }
    Copy-Item $arc $orig
}
if ((Sha $orig SHA256) -ne $man.rels_arc_original_sha256) { Fail "rels.arc.original was changed; delete the game folder and run setup again" }
if ((Sha "$here\mod\d_a_morita_testNP.rel" SHA256) -ne $man.puppet_rel_sha256) { Fail "mod\d_a_morita_testNP.rel is damaged" }
$work = "$game\rels-work"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
& $exe u8 unpack $orig $work
Copy-Item "$here\mod\d_a_morita_testNP.rel" "$work\$($man.puppet_rel_name -replace '/', '\')" -Force
& $exe u8 pack $work $arc --like $orig | Out-Null
Remove-Item -Recurse -Force $work
if ((Sha $arc SHA256) -ne $man.rels_arc_patched_sha256) { Fail "patched rels.arc has an unexpected hash" }
Write-Host "Puppet module installed"

# 4. Dolphin settings folder: the spawn hook (Gecko code) and cheats on. Controls are set up inside Dolphin.
New-Item -ItemType Directory -Force "$user\GameSettings" | Out-Null
Copy-Item "$here\mod\SOUE01.ini" "$user\GameSettings\SOUE01.ini" -Force
& $exe cheats $user | Out-Null
if ($Save) {
    # A save is three files (wiiking2.sav, skip.dat, banner.bin). With only wiiking2.sav the game creates a new,
    # empty save on boot (seen 2026-10-25), so the whole folder is copied. -Save may name the folder or the .sav.
    $src = if ((Get-Item $Save).PSIsContainer) { $Save } else { Split-Path $Save -Parent }
    foreach ($f in 'wiiking2.sav', 'skip.dat', 'banner.bin') {
        if (-not (Test-Path "$src\$f")) { Fail "save folder $src is missing $f (copy all three files)" }
    }
    $saveDir = "$user\Wii\title\00010000\534f5545\data"
    if (Test-Path $saveDir) {
        $bak = "$saveDir-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Copy-Item $saveDir $bak -Recurse
        Write-Host "Previous save kept in $bak"
    }
    New-Item -ItemType Directory -Force $saveDir | Out-Null
    foreach ($f in 'wiiking2.sav', 'skip.dat', 'banner.bin') { Copy-Item "$src\$f" "$saveDir\$f" -Force }
    Write-Host "Save installed"
}
Write-Host "Setup done. Next: double-click '2 Host.cmd' (host) or '3 Join.cmd' (joining player)." -ForegroundColor Green
