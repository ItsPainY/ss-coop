# Skyward Sword co-op (v0.9, early test)

Two players, each in their own Dolphin, see each other's Link and share story progress.

**What works:** the other player's Link appears in your world and follows them smoothly: with head, face and
hands, in the outfit they are wearing, and playing the same animations they do (walking, running, rolling,
jumping, side steps, climbing, swimming, sword swings; upper and lower body separately), carrying their sword,
sheath and shield (in hand or on the back, whichever they have equipped). Story, item and area flags
are shared: talk to someone or trigger an event, and the other player's game gets the same progress. It also
ends up in their save file. Dungeon flags (small keys, map, boss key) and chests are shared too: a key one of you
picks up is there for both, using it uses it for both, and a chest one of you opens is open for both.

**Enemies (new, first version):** each enemy is run by the game of the player nearer to it; in the other game it
stands where it really is and takes damage from both of you (its health is the lower of the two). For Keese, ChuChus,
Remlits and Skytails it also plays the same animation, attacks included; other enemies (for example Bokoblins) still
animate separately in each game. An enemy only damages the Link in its own game.

**Not yet:** animation sync for other enemy types, enemies hitting the other player, bosses, cutscenes (each player sees their own), moving parts of
held items (the bow string, a clawshot or whip flying out: the other Link holds them, they don't move on their own), and
animations that come with special items or situations.

**New in 0.9:** Keese, ChuChus, Remlits and Skytails move the same way in both games, attacks included.
**New in 0.8:** enemies stand in the same place in both games and share damage. **New in 0.7:** the other Link holds their items: bow, slingshot, beetle, clawshots, whip, Gust Bellows, harp,
bug net, bottles, mole gloves, sailcloth. **New in 0.6:** defeated enemies are shared while you are both in the same area: an enemy the other player
defeats disappears in your game too (it vanishes, no death animation and no item drop for you) and doesn't come
back until you leave the area, as in the normal game. **New in 0.5:** the other Link carries their sword, sheath and shield. When they change sword or shield, the
other Link briefly disappears and comes back with the new one. **New in 0.4:** the other Link plays the other player's real animations. **New in 0.3:** dungeon keys/maps and chests are shared. **New in 0.2:** the other Link has a head, hands,
animations and the right outfit. Different versions can't connect to each other: **both players need 0.9.**

## Updating from an earlier version

1. Unzip the new zip. Copy everything **inside** its `ss-coop` folder into your existing folder
   (e.g. `ss-coop-v0.1`) and let Windows replace the files.
2. Double-click **`1 Setup.cmd`**. It sees the game is already set up and only updates the mod files: no disc
   needed, your save and controls stay.

This package contains **no game files**. Each player needs their own dump of their own disc.

## What each player needs

- Windows 10 or 11.
- **Your own dump of The Legend of Zelda: Skyward Sword, Wii, USA, game ID SOUE01, revision 0**, as an
  .rvz or .iso. The setup checks this and stops if it's a different version.
- **Dolphin 2609a** from <https://dolphin-emu.org/download/> (Windows x64, release 2609a). Other versions may not
  work.
- No Python needed: the helpers are bundled in `sscoop.exe`. Its source is included (`bridge\`, `tools\`).
  The exe is not signed, so Windows SmartScreen or an antivirus may warn about it. "More info → Run anyway"
  is fine if you got the zip directly from the host.
- Recommended: the **host's save folder** (three files: `wiiking2.sav`, `skip.dat`, `banner.bin`), so you both
  start with the same progress.

## Setup (once, both players)

1. Unzip this package somewhere, e.g. `C:\Games\ss-coop-v0.1`.
2. Unzip Dolphin 2609a **into this folder**, so that `ss-coop-v0.1\Dolphin-x64\Dolphin.exe` exists.
3. Joining player: unzip the host's `host-save.zip` into this folder too (gives `host-save\` with three files).
4. Double-click **`1 Setup.cmd`** and pick your disc dump in the window that opens. Wait for the green
   "Setup done". If `host-save\` is there, it's used for your first setup (never again afterwards, so it can't
   overwrite your progress).

   (Typed version: `powershell -ExecutionPolicy Bypass -File setup.ps1 -Disc "C:\path\to\dump.rvz" [-Save folder]`.)

   The setup:
   - checks your disc,
   - extracts the game into `game\` (about 4.7 GB, not your disc file itself),
   - installs the co-op module (your original archive is kept as `game\rels.arc.original`),
   - prepares a separate Dolphin settings folder `dolphin-user\`. Your normal Dolphin is not touched.

4. **Controls:** the first time you play (step below), open **Controllers** in Dolphin and set up
   Wii Remote 1 (Emulated, with Nunchuk and MotionPlus). These settings are separate from any other Dolphin.

## Playing

- **Host** (the player whose router has port forwarding): double-click **`2 Host.cmd`**.
- **Joining player:** double-click **`3 Join.cmd`** and type the host's public IP when asked (just the
  numbers, no quotes, no port). Next time, Enter alone reuses the last address.

(Typed versions: `play.ps1 host` and `play.ps1 join <IP>`.)

The window starts Dolphin, waits until the game runs, then connects. **Leave the PowerShell window open** while
you play. Closing it, or pressing Ctrl+C, disconnects. Then load your save in the game. As soon as both of you
are in the same area, you'll see each other.

The joiner can connect before or after the host. If the connection drops, just start `play.ps1` again.
If Windows asks whether **sscoop.exe** may communicate on networks, allow it (both players may see this).

## Port forwarding (host only, once)

- In your router, forward **UDP port 27645** to the host PC's local IP address.
  - Example for a FRITZ!Box: Internet → Permit Access → Port Sharing → add the PC → New Sharing → Port sharing,
    protocol **UDP**, port 27645 to 27645.
- The first time you host, **Windows asks whether sscoop.exe may communicate on networks: allow it.** If you
  miss the prompt: Windows Security → Firewall → "Allow an app through firewall" → sscoop.exe → tick Private
  (and Public, if your network is set to Public).
- Your **public IP** is what you give the joining player. It's shown in the router, or search for
  "what is my IP" in a browser.

## Good to know

- Start from the **same save** (the host's). Some progress values are counters, and merging two different saves
  can mix them up.
- Loading an older save or resetting the game never erases the other player's progress; your game gets theirs
  back.
- Cutscenes and dialogue play only for the player who triggers them. The other player gets the progress quietly.
- Each player saves in their own game, as usual.
- To uninstall: delete this folder. Nothing else on the PC was changed (except the firewall rule, if you allowed it).

## If something goes wrong

- `SETUP FAILED: this is not SOUE01` / `wrong revision`: your disc is a different version (Europe, Japan or a later
  revision). This build only supports the first USA release.
- `The game did not start with the co-op hook`: wrong Dolphin version, or `dolphin-user\Config\Dolphin.ini` lost
  `EnableCheats = True`. Run setup again.
- Joining shows `peer: -` forever: the host isn't running, port forwarding/firewall is blocking, or the IP is
  wrong. Test on the same network first with the host's local IP (e.g. `192.168.x.x`).
- You don't see each other: you must be in the **same area** (same stage).
