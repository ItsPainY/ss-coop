# Build the co-op puppet REL with the decomp toolchain and pack it into the extracted game copy.
# The original rels.arc stays in game\backup-orig and is the base for every repack.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$decomp = "$root\decomp\ss"
$tools = "..\..\tools\decomp-tools"   # relative: the project path has a space, build.ninja does not quote it
$enc = New-Object Text.UTF8Encoding $false

# 1. Sources into the decomp branch (no BOM: mwcc is picky).
foreach ($pair in @(@("mod\puppet\d_a_coop_puppet.cpp", "src\REL\coop\d_a_coop_puppet.cpp"),
                    @("mod\puppet\d_a_coop_puppet.h", "include\coop\d_a_coop_puppet.h"),
                    @("mod\puppet\held_models.inc", "include\coop\held_models.inc"))) {
    $text = [IO.File]::ReadAllText("$root\$($pair[0])").TrimStart([char]0xFEFF)
    [IO.File]::WriteAllText("$decomp\$($pair[1])", $text, $enc)
}

# 2. Build. The checksum step is expected to fail for our REL only.
Push-Location $decomp
try {
    $ErrorActionPreference = 'Continue'   # native tools write warnings to stderr; judge by output instead
    $env:PATH = "$((Resolve-Path "$tools\ninja").Path);$env:PATH"
    $log = & ninja -k 0 2>&1 | Out-String
    if ($log -match 'MWCC.*\r?\nFAILED|error:|Error   :') { Write-Host $log; throw "compile failed" }
    # The REL link step can fail on its own (e.g. a symbol the game binary does not contain) while the checksum
    # step below still passes on the previous REL (seen 2026-10-25). Build it explicitly and check its age.
    $relTarget = "build/SOUE01/d_a_morita_testNP/d_a_morita_testNP.rel"
    $relLog = & ninja $relTarget 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { Write-Host $relLog; throw "REL link failed" }
    if ((Get-Item $relTarget).LastWriteTime -lt (Get-Item "build/SOUE01/src/REL/d/a/d_a_morita_test.o").LastWriteTime) {
        throw "REL is older than its object file: not relinked"
    }
    $check = & "$tools\dtk.exe" shasum -c config\SOUE01\build.sha1 2>&1 | Out-String
    # dtk colors its output when it thinks it has a terminal (": <ESC>[31mFAILED"): strip that before matching.
    $check = $check -replace "\x1b\[[0-9;]*m", ""
    $bad = @($check -split "`r?`n" | Where-Object { $_ -match ': FAILED' })
    if ($bad.Count -ne 1 -or $bad[0] -notmatch 'd_a_morita_testNP') { Write-Host $check; throw "unexpected modules changed" }
    Write-Host "Build OK: only d_a_morita_testNP differs from the original"
} finally { Pop-Location }

# 3. Repack rels.arc from the original with our REL swapped in.
$bk = "$root\game\backup-orig\rels.arc"
$w = "$root\game\rels-work"
if (Test-Path $w) { Remove-Item -Recurse -Force $w }
python -I "$root\mod\tools\u8.py" unpack $bk $w
# The REL gets a fixed 32 KiB slot (zero padded), so the archive layout never changes between builds:
# mod/tools/devloop.py can then swap a new build into a savestate's RAM copy of rels.arc.
$rel = [IO.File]::ReadAllBytes("$decomp\build\SOUE01\d_a_morita_testNP\d_a_morita_testNP.rel")
$slot = 0x8000
if ($rel.Length -gt $slot) { throw "puppet REL is $($rel.Length) bytes, larger than its $slot-byte slot" }
$padded = New-Object byte[] $slot
[Array]::Copy($rel, $padded, $rel.Length)
[IO.File]::WriteAllBytes("$w\rels\d_a_morita_testNP.rel", $padded)
Copy-Item "$decomp\build\SOUE01\d_a_morita_testNP\d_a_morita_testNP.rel" "$root\game\puppet-latest.rel" -Force
python -I "$root\mod\tools\u8.py" pack $w "$root\game\extracted\DATA\files\rels.arc" --like $bk
Remove-Item -Recurse -Force $w
