# Build sscoop.exe (all Python helpers in one file) into the release package.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$pkg = "$root\release\ss-coop"
$venv = "$root\tools\exe-build"
$work = "$root\release\build\work"
& "$venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --console --name sscoop `
    --paths "$pkg\bridge" --paths "$pkg\tools" `
    --hidden-import net_bridge --hidden-import game_side --hidden-import flag_sync --hidden-import enemy_sync --hidden-import held_models --hidden-import enemy_motion `
    --hidden-import ss_mem --hidden-import u8 --hidden-import enable_cheats --hidden-import wait_game `
    --distpath $pkg --workpath "$work\build" --specpath $work `
    "$root\release\build\sscoop.py"
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
Get-Item "$pkg\sscoop.exe" | Select-Object Name, Length
