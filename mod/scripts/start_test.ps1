# Boot the modded game copy in its own Dolphin user dir, with the puppet spawn hook enabled.
# The owner's normal Dolphin config and save are only COPIED (once) into the test dir, never changed.
param(
    [Parameter(Mandatory = $true)][string]$PuppetProfileHex,
    [ValidateSet('A', 'B')][string]$Instance = 'A',   # A and B each get their own user dir (two players, one PC)
    [switch]$RefreshFromOwner,     # re-copy controller config and save from the owner's Dolphin
    [switch]$FromState             # boot straight into StateSaves\SOUE01.s01 (Shift+F1 in that window).
                                   # Only valid for the puppet build the state was taken with: a state holds
                                   # the loaded REL and the hook block (see NOTES.md, savestate dev loop).
)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
$dolphin = "$root\tools\Dolphin-x64"
$ownerUser = "$dolphin\User"
$testUser = if ($Instance -eq 'A') { "$root\game\dolphin-coop-test" } else { "$root\game\dolphin-coop-test-b" }
$saveRel = "Wii\title\00010000\534f5545\data"

if ($RefreshFromOwner -or -not (Test-Path "$testUser\Config\WiimoteNew.ini")) {
    New-Item -ItemType Directory -Force "$testUser\Config", "$testUser\$saveRel" | Out-Null
    foreach ($f in "Dolphin.ini", "WiimoteNew.ini", "GFX.ini") {
        if (Test-Path "$ownerUser\Config\$f") { Copy-Item "$ownerUser\Config\$f" "$testUser\Config\$f" -Force }
    }
    if (Test-Path "$ownerUser\Config\Profiles") { Copy-Item "$ownerUser\Config\Profiles" "$testUser\Config\" -Recurse -Force }
    # B starts from A's save (the test progress), A from the owner's save.
    $saveFrom = if ($Instance -eq 'B' -and (Test-Path "$root\game\dolphin-coop-test\$saveRel")) { "$root\game\dolphin-coop-test" } else { $ownerUser }
    if (Test-Path "$saveFrom\$saveRel") { Copy-Item "$saveFrom\$saveRel\*" "$testUser\$saveRel\" -Force }
    Write-Host "Copied controller config and save into $testUser"
}

python -I "$root\mod\hook\build_gecko.py" $PuppetProfileHex $testUser | Out-Null
Write-Host "Spawn hook written (profile $PuppetProfileHex), cheats enabled in test dir"

$dolphinArgs = @('-u', "`"$testUser`"", '-e', "`"$root\game\extracted\DATA\sys\main.dol`"")
$state = "$testUser\StateSaves\SOUE01.s01"
if ($FromState) {
    if (-not (Test-Path $state)) { throw "no savestate at $state (load the save, press Shift+F1)" }
    $dolphinArgs += @('-s', "`"$state`"")
}
$p = Start-Process "$dolphin\Dolphin.exe" -ArgumentList $dolphinArgs -PassThru
Write-Host "Dolphin test instance $Instance started, pid $($p.Id)"
