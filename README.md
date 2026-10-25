# ss-coop: online co-op for The Legend of Zelda: Skyward Sword (Wii, Dolphin)

Two players, each in their own Dolphin, see each other as a second Link and share progress. Built with the
[Co-op Anything](https://github.com/cranebeaker/coop-anything) playbook; the full write-up (how the game works for co-op,
what was changed, QA results, known issues) is in
[games/skyward-sword](https://github.com/cranebeaker/coop-anything/tree/main/games/skyward-sword).

**This repository contains no game files.** Every player needs their own dump of their own disc:
The Legend of Zelda: Skyward Sword, Wii, USA, game ID `SOUE01`, revision 0.

## What is shared

- The other player's Link: head, face, hands, outfit, real animations, sword, sheath, shield, held items.
- Story, item and area flags, dungeon keys and maps, chests, defeated enemies; written to both save files.
  Loading an older save or resetting never erases the other player's progress.
- Living enemies: owned by the nearer player, shown at the owner's position in the other game, with shared health;
  Keese, ChuChus, Remlits and Skytails also with the owner's animation.

Not shared: enemies hitting the other player, bosses, cutscenes, other enemy types' animations.

## Layout

| Folder | What |
|---|---|
| `mod/puppet/` | The second-Link actor (C++, built with the [zeldaret/ss](https://github.com/zeldaret/ss) decomp toolchain into the REL of the unused `J_TEST` actor) |
| `mod/hook/` | Gecko C2 hook that spawns the actor each stage and holds the shared mailbox (`spawn_puppet.s`, `build_gecko.py`) |
| `mod/bridge/` | The bridge next to each Dolphin: memory access, UDP host/join, flag, enemy and ownership sync, and their tests |
| `mod/qa/` | `coop_qa.py`: two-Dolphin bot test with no human input |
| `mod/tools/` | Memory, archive and debugging helpers (`ss_mem.py`, `u8.py`, `nlz.py`, `actor_list.py`, ...) |
| `mod/scripts/` | Build the REL into an extracted game copy; start labelled test instances |
| `release/` | The player package: setup, host/join launchers, README, `sscoop.exe` build script |
| `decomp/coop-puppet.patch` | The change to zeldaret/ss (base commit `96f2dd54`) that compiles our actor in place of `J_TEST` |

## Building

1. Clone [zeldaret/ss](https://github.com/zeldaret/ss) at `96f2dd54`, apply `decomp/coop-puppet.patch`, put your own
   disc's files where the decomp expects them and check it builds byte-identical (`dtk shasum -c`).
2. Extract your disc into `game/extracted` (Dolphin: right-click the game, Extract Entire Disc) and keep a copy of the
   original `rels.arc` in `game/backup-orig/`.
3. `powershell -File mod/scripts/build_mod.ps1` copies the sources into the decomp, builds, checks that only our module
   differs from the original, and repacks `rels.arc` in the extracted copy.
4. For the player package: copy the bridge and tools into `release/`, the REL into `release/mod/`, update
   `release/mod/manifest.json`, and build `sscoop.exe` with `release/build/build_exe.ps1` (PyInstaller).

## Testing

- Without the game: `python -I mod/bridge/test_net_bridge.py`, `test_flag_sync.py`, `test_enemy_sync.py`,
  `test_enemy_motion.py` (add `--chaos` to the first two for lossy-network runs).
- With two real games: make savestates in both test instances (`mod/scripts/start_test.ps1 -Instance A|B`, load the
  save, Shift+F1), then `python -I mod/qa/coop_qa.py`. It prints `RESULT: PASS` or `RESULT: FAIL`;
  `--mutant NAME` runs a deliberately broken bridge that must fail.

## License

The code here is released under CC0 1.0 (see `LICENSE`), like the zeldaret/ss decompilation it is built on.
Skyward Sword is a trademark of Nintendo; this project is not affiliated with Nintendo.
