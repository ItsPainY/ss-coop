"""Two-player QA for the Skyward Sword co-op mod. No human input: drives both games through their memory.

  python -I coop_qa.py [--expect-build 11] [--keep-open] [--mutant NAME]

Boots test Dolphins A and B from their savestates (game/dolphin-coop-test[-b]/StateSaves/SOUE01.s01, which must
come from the expected puppet build), connects host and join bridges over loopback, runs the gates, cleans up,
and prints one line: RESULT: PASS / RESULT: FAIL.

What the bot does: teleports Link (writes dAcBase_c::mPosition), sets and clears test flag bits (live arrays,
like the game does), toggles mTunicType, kills and restarts a bridge. What it cannot do: press buttons, so real
player actions (a roll, a sword swing, a door) are not covered here; see NOTES.md (QA gaps).

--mutant NAME runs the bridges with COOP_MUT=NAME (no_position, no_flag_apply, no_mass_clear_brake, no_enemy_apply, no_held, no_epoch, no_enemy_anim): the matching
gates must FAIL. That is how we know the gates can fail at all (playbook: mutation twins).
"""

import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "mod", "bridge"))
sys.path.insert(0, os.path.join(ROOT, "mod", "tools"))
import ss_mem as m  # noqa: E402
import actor_list  # noqa: E402
from game_side import GameSide, FLAG_ARRAYS, SCENE_MIRRORS, ENEMY_LIVE, ENEMY_SAVE  # noqa: E402

BLOCK_PUPPET, BLOCK_FLAGS, BLOCK_ROT, BLOCK_BUILD, BLOCK_ANIM = 0x00, 0x1C, 0x2C, 0x2F, 0x40
LINK_ROT_Y, LINK_TUNIC, LINK_ANIMS = 0xBA, 0x120B, 0x121A
OBJ_FLAG = 0x1D5                 # fProfile::OBJ_FLAG: a harmless placed object in Skyloft, used as the "enemy"
TEST_DIRS = {"A": os.path.join(ROOT, "game", "dolphin-coop-test"), "B": os.path.join(ROOT, "game", "dolphin-coop-test-b")}
# Saves the run must leave untouched (hash before/after): the test Dolphin's own default user folder, plus one path per
# line in local/protect_saves.txt (not published: it names the owner's own play folder).
OWNER_SAVES = [os.path.join(ROOT, "tools", "Dolphin-x64", "User", "Wii", "title", "00010000", "534f5545", "data",
                            "wiiking2.sav")]
_protect = os.path.join(ROOT, "local", "protect_saves.txt")
if os.path.exists(_protect):
    OWNER_SAVES += [os.path.join(ROOT, line.strip()) for line in open(_protect) if line.strip()]
OUT = os.path.join(ROOT, "game", "qa-out")


class Timeout(Exception):
    pass


def wait_until(fn, timeout, step=0.1):
    """Poll fn() until truthy (returns it) or raise Timeout with the last value."""
    end, last = time.time() + timeout, None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise Timeout(f"still {last!r} after {timeout}s")


class Game:
    """One test Dolphin: GameSide for Link/flags plus raw access to the hook block and the puppet."""

    def __init__(self, name, pid):
        self.name, self.pid = name, pid
        self.side = GameSide(pid)
        self.d = m.Dolphin(writable=True, pid=pid)
        self.block = self.side.block

    def u32(self, a):
        return self.d.u32(a)

    def link(self):
        return self.d.u32(m.LINK_PTR)

    def link_pos(self):
        return struct.unpack(">3f", self.d.read(self.link() + 0xC0, 12))

    def set_link_pos(self, p):
        self.d.write(self.link() + 0xC0, struct.pack(">3f", *p))

    def puppet(self):
        return self.u32(self.block + BLOCK_PUPPET)

    def puppet_pos(self):
        p = self.puppet()
        return struct.unpack(">3f", self.d.read(p + 0xC0, 12)) if m.valid_ptr(p) else None

    def mail_flags(self):
        return self.u32(self.block + BLOCK_FLAGS)

    def build(self):
        return self.d.read(self.block + BLOCK_BUILD, 1)[0]

    def events(self):
        b = self.d.read(self.block, 0x40)
        hidx = struct.unpack(">I", b[0x38:0x3C])[0]
        return [b[0x30 + ((hidx - min(hidx, 8) + i) & 7)] for i in range(min(hidx, 8))], hidx

    def alive(self):
        """The game advances frames: the puppet's execute counter or Link's animation frame moves."""
        def sample():
            link = self.link()
            frame = self.d.read(link + 0x728 + 0x30, 4) if m.valid_ptr(link) else None
            return self.u32(self.block + 0x10), frame
        a = sample()
        time.sleep(0.5)
        b = sample()
        return a[0] != b[0] or a[1] != b[1]

    # flags: live mirror word of the current scene entry (as the game would change it)
    def live_word(self, name, i):
        off, n, live = FLAG_ARRAYS[name]
        if live:
            return self.d.u16s(live + 2 * i, 1)[0]
        addr, per = SCENE_MIRRORS[name]
        return self.d.u16s(addr + 2 * (i % per), 1)[0]

    def set_live_word(self, name, i, v):
        off, n, live = FLAG_ARRAYS[name]
        addr = live + 2 * i if live else SCENE_MIRRORS[name][0] + 2 * (i % SCENE_MIRRORS[name][1])
        self.d.write(addr, struct.pack(">H", v))

    def save_word(self, name, i):
        return self.d.u16s(self.side._file_a() + FLAG_ARRAYS[name][0] + 2 * i, 1)[0]

    def set_save_word(self, name, i, v):
        self.d.write(self.side._file_a() + FLAG_ARRAYS[name][0] + 2 * i, struct.pack(">H", v))


def heap_free_work1(g):
    head = g.u32(g.u32(0x805751A8) + 0x10)
    blk, free = g.u32(head + 0x3C), 0
    for _ in range(10000):
        if not blk:
            break
        sig, size, nxt = struct.unpack(">H2xI4xI", g.d.read(blk, 0x10))
        if sig != 0x4652:
            return None
        free, blk = free + size, nxt
    return free


class Run:
    def __init__(self, args):
        self.args = args
        self.results = []
        self.procs = []
        self.bridges = {}
        self.port = None
        self.env = dict(os.environ)
        if args.mutant:
            self.env["COOP_MUT"] = args.mutant

    # --- infrastructure ---
    def launch(self, inst):
        state = os.path.join(TEST_DIRS[inst], "StateSaves", "SOUE01.s01")
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              os.path.join(ROOT, "mod", "scripts", "start_test.ps1"), "-PuppetProfileHex", "14E",
                              "-Instance", inst, "-FromState"], capture_output=True, text=True).stdout
        pid = int(out.rsplit("pid", 1)[1].split()[0])
        self.procs.append(pid)
        return pid, state

    def start_bridge(self, role, pid):
        nb = os.path.join(ROOT, "mod", "bridge", "net_bridge.py")
        log = open(os.path.join(OUT, f"bridge-{role}.txt"), "w")
        if role == "host":
            cmd = [sys.executable, "-I", nb, "--pid", str(pid), "host", "--bind", "127.0.0.1", "--port", str(self.port)]
        else:
            cmd = [sys.executable, "-I", nb, "--pid", str(pid), "join", f"127.0.0.1:{self.port}"]
        self.bridges[role] = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=self.env)

    def stop_bridge(self, role):
        p = self.bridges.pop(role, None)
        if p:
            p.terminate()
            p.wait(5)

    def gate(self, name, fn):
        t0 = time.time()
        try:
            detail = fn() or ""
            status = "PASS"
        except Timeout as e:
            status, detail = "TIMEOUT", str(e)
        except AssertionError as e:
            status, detail = "FAIL", str(e)
        except Exception as e:  # noqa: BLE001 - a crashing gate is a failing gate
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
        self.results.append({"gate": name, "status": status, "detail": detail, "seconds": round(time.time() - t0, 1)})
        print(f"{status:7} {name:28} {detail}", flush=True)
        return status == "PASS"

    # --- gates ---
    def g_scorer_selftest(self):
        assert score([{"status": "PASS"}, {"status": "FAIL"}]) == "FAIL"
        assert score([{"status": "PASS"}, {"status": "TIMEOUT"}]) == "FAIL"
        assert score([]) == "FAIL", "no results must not pass"
        assert score([{"status": "PASS"}, {"status": "PASS"}]) == "PASS"
        return "bad inputs score FAIL"

    def g_boot(self):
        for g in (self.a, self.b):
            wait_until(lambda: m.valid_ptr(g.link()) and g.side.flag_view() is not None, 30)
            wait_until(lambda: m.valid_ptr(g.puppet()), 15)
            assert g.build() == self.args.expect_build, f"{g.name} runs puppet build {g.build()}, expected " \
                f"{self.args.expect_build}: savestate is from another build"
            assert g.alive(), f"{g.name} is not running"
        sa, sb = self.a.side.link_state(), self.b.side.link_state()
        assert sa[0] == sb[0], f"different stages {sa[0]} / {sb[0]}"
        return f"both in {sa[0]}, puppet build {self.args.expect_build}"

    def g_connect(self):
        wait_until(lambda: self.a.mail_flags() & 7 == 7 and self.b.mail_flags() & 7 == 7, 15)
        return "mail flags 7 (bridge, visible, animation) on both"

    def _teleport_check(self, mover, viewer):
        start = mover.link_pos()
        target = (start[0] + 60.0, start[1], start[2])
        mover.set_link_pos(target)
        try:
            time.sleep(0.3)
            now = mover.link_pos()
            assert m_dist(now, start) > 40, f"precondition: teleport did not stick ({now} vs start {start})"
            last = {}

            def close():
                p, q = viewer.puppet_pos(), mover.link_pos()
                last["gap"], last["p"], last["q"] = (m_dist(p, q) if p else None), p, q
                return last["gap"] is not None and last["gap"] < 10 and (last["gap"],)
            try:
                gap, = wait_until(close, 4)
            except Timeout:
                raise AssertionError(f"puppet {_r(last['p'])} vs Link {_r(last['q'])}: gap {last['gap']}")
            return f"{viewer.name}'s puppet within {gap:.1f} units of {mover.name}'s Link moved {m_dist(now, start):.0f}"
        finally:
            mover.set_link_pos(start)

    def g_position_a_to_b(self):
        return self._teleport_check(self.a, self.b)

    def g_position_b_to_a(self):
        return self._teleport_check(self.b, self.a)

    def g_facing_and_anim(self):
        def match():
            link = self.a.link()
            rot = struct.unpack(">h", self.a.d.read(link + LINK_ROT_Y, 2))[0]
            anims = struct.unpack(">2H", self.a.d.read(link + LINK_ANIMS, 4))
            mrot = struct.unpack(">h", self.b.d.read(self.b.block + BLOCK_ROT, 2))[0]
            manim = struct.unpack(">2H", self.b.d.read(self.b.block + BLOCK_ANIM, 4))
            return (rot, anims) if abs(rot - mrot) <= 1 and anims == manim else None
        rot, anims = wait_until(match, 3)
        return f"B's mailbox has A's facing {rot} and animations {anims}"

    def g_equipment(self):
        """B's mailbox carries A's sword/sheath/shield state exactly (drawn bits, ids, matrices), and no puppet
        failed to load an equipment model (event 12)."""
        def same():
            mine = self.a.side.equip_state()
            got = self.b.d.read(self.b.block + 0x60, len(mine))
            return (mine,) if mine == got and self.b.mail_flags() & 8 else None
        mine, = wait_until(same, 3)
        assert 12 not in self.b.events()[0], "puppet could not load an equipment model"
        return f"drawn bits {mine[0]}, sword {mine[1]}, shield {mine[2]} reach B's mailbox unchanged"

    def g_held_items(self):
        """B's mailbox carries A's held item state exactly (drawn bits, model ids, matrices), and no puppet failed to
        load a held item model (event 15)."""
        def same():
            mine = self.a.side.held_state()
            got = self.b.d.read(self.b.block + 0xD0, len(mine))
            return (mine,) if mine == got and self.b.mail_flags() & 16 else None
        mine, = wait_until(same, 3)
        assert 15 not in self.b.events()[0], "puppet could not load a held item model"
        return f"drawn bits {mine[0]}, ids {list(mine[1:4])} reach B's mailbox unchanged"

    def g_enemy_kill(self):
        """A defeats a placed object (its enemy defeat bit is set, as dAcBase_c::killWithFlag does): B gets the bit
        (live and committed copy, so it does not respawn) and the live object disappears from B's world."""
        def bit(g, oid, save=False):
            base = g.side._file_a() + ENEMY_SAVE if save else ENEMY_LIVE
            return bool(g.d.u16s(base + 2 * (oid // 16), 1)[0] & (1 << (oid % 16)))
        alive_b = {r["obj_id"]: r for r in actor_list.actors(self.b.d) if r["state"] == 1}
        candidates = [r["obj_id"] for r in actor_list.actors(self.a.d)
                      if r["profile"] == OBJ_FLAG and r["obj_id"] in alive_b and r["obj_id"] != 0xFFFF
                      and not bit(self.a, r["obj_id"]) and not bit(self.b, r["obj_id"])]
        assert candidates, "precondition: no OBJ_FLAG with a free defeat bit in both games"
        oid = candidates[0]
        ev0 = self.b.events()[1]
        i = oid // 16
        self.a.side.enemy_write(i, self.a.d.u16s(ENEMY_LIVE + 2 * i, 1)[0] | (1 << (oid % 16)))
        wait_until(lambda: bit(self.b, oid) and bit(self.b, oid, True), 5)
        wait_until(lambda: not any(r["obj_id"] == oid and r["state"] == 1 for r in actor_list.actors(self.b.d)), 5)
        ev, n = self.b.events()
        assert n == ev0 + 1 and ev[-1] == 13, f"puppet events {ev} ({n - ev0} new), expected one 'remote kill'"
        return f"object {oid:#06x} defeated in A: bit in B (live + save), gone from B's world, one removal"

    def _orders(self, g):
        t = g.u32(g.block + 0x120)
        if not m.valid_ptr(t):
            return {}
        n = min(g.u32(t + 0x388), 32)
        raw = g.d.read(t + 0x38C, n * 28) if n else b""
        out = {}
        for k in range(n):
            oid, rot, x, y, z, cap, flags, anim, mode, seq, frame = struct.unpack_from(">Hh3fHHHBBf", raw, k * 28)
            out[oid] = flags
        return out

    def g_enemy_motion(self):
        """A real enemy in both games (the table the puppet publishes). The nearer player's game owns it and the
        other game's copy follows: nudging the owner's copy moves the other one. Moving B's Link next to it hands it
        to B, and the following turns around."""
        ta = {e["objId"]: e for e in (self.a.side.enemy_table() or [])}
        tb = {e["objId"]: e for e in (self.b.side.enemy_table() or [])}
        both = sorted(set(ta) & set(tb), key=lambda o: (ta[o]["profile"] != 0x170, o))   # prefer Remlits
        assert both, "precondition: no shared enemy listed in both games"
        oid = both[0]

        def actor(g):
            return next((r for r in actor_list.actors(g.d) if r["obj_id"] == oid and r["state"] == 1), None)

        def nudge(g, dx):
            r = actor(g)
            g.d.write(r["addr"] + 0xC0, struct.pack(">3f", r["pos"][0] + dx, r["pos"][1], r["pos"][2]))
            return (r["pos"][0] + dx, r["pos"][1], r["pos"][2])

        why = {}

        def follows(follower, owner, near=None):
            of, oo = self._orders(follower).get(oid, 0), self._orders(owner).get(oid, 0)
            af, ao = actor(follower), actor(owner)
            why["last"] = (follower.name, of, owner.name, oo, af and af["pos"], ao and ao["pos"])
            if not (of & 1) or oo & 1 or af is None or ao is None:
                return None
            gap = m_dist(af["pos"], ao["pos"])
            if gap >= 15 or (near is not None and m_dist(ao["pos"], near) >= 15):
                return None
            return (gap,)

        def waited(fn, timeout):
            try:
                return wait_until(fn, timeout)
            except Timeout:
                raise AssertionError(f"waiting: follower, its order flags, owner, its flags, positions = {why['last']}")
        def owner_now():
            for owner, follower in ((self.a, self.b), (self.b, self.a)):
                if follows(follower, owner):
                    return (owner, follower)
            return None
        owner, follower = waited(owner_now, 6)
        start = follower.link_pos()
        try:
            moved = nudge(owner, 30.0)
            gap1, = waited(lambda: follows(follower, owner, moved), 4)
            e = actor(follower)["pos"]
            follower.set_link_pos((e[0] + 60, e[1], e[2]))                     # the other player walks up to it
            waited(lambda: follows(owner, follower), 8)
            moved = nudge(follower, -30.0)
            gap2, = waited(lambda: follows(owner, follower, moved), 4)
            return (f"enemy {oid:#06x} (profile {ta[oid]['profile']:#x}): owner {owner.name}, {follower.name} followed a "
                    f"nudge (gap {gap1:.1f}); {follower.name}'s Link came close, owner {follower.name}, "
                    f"{owner.name} followed (gap {gap2:.1f})")
        finally:
            follower.set_link_pos(start)

    def g_enemy_animation(self):
        """The owner's enemy plays another animation (dev command Shared::testAnimObj/Idx); the other game's copy
        switches to it and keeps close to the owner's frame."""
        def table(g):
            return {e["objId"]: e for e in (g.side.enemy_table() or [])}
        both = [o for o in table(self.a) if o in table(self.b) and table(self.a)[o]["anim"] != 0xFFFF]
        assert both, "precondition: no enemy of a type with animation sync in both games"
        oid = both[0]

        def roles():
            fa, fb = self._orders(self.a).get(oid, 0), self._orders(self.b).get(oid, 0)
            if fb & 2 and not fa & 1:
                return (self.a, self.b)
            if fa & 2 and not fb & 1:
                return (self.b, self.a)
            return None
        owner, follower = wait_until(roles, 6)
        cur = table(owner)[oid]["anim"]
        want = 1 if (cur & 0x7FFF) == 0 else 0
        owner.d.write(owner.block + 0x128, struct.pack(">HH", oid, want))
        wait_until(lambda: table(owner)[oid]["anim"] == want, 3)          # precondition: the command took
        last = {}

        def synced():
            o, f = table(owner)[oid], table(follower)[oid]
            last["v"] = (o["anim"], o["frame"], f["anim"], f["frame"])
            return (abs(o["frame"] - f["frame"]),) if f["anim"] == want and abs(o["frame"] - f["frame"]) < 8 else None
        try:
            diff, = wait_until(synced, 4)
        except Timeout:
            raise AssertionError(f"owner anim/frame, follower anim/frame = {last.get('v')}")
        return (f"enemy {oid:#06x}: owner {owner.name} switched animation {cur:#x} -> {want:#x}, "
                f"{follower.name}'s copy followed (frame difference {diff:.1f})")

    def _free_word(self, name, start):
        n = FLAG_ARRAYS[name][1]
        for i in range(start, 0, -1):
            if self.a.live_word(name, i) == 0 and self.b.live_word(name, i) == 0 and \
                    self.a.save_word(name, i) == 0 and self.b.save_word(name, i) == 0:
                return i
        raise AssertionError(f"no free {name} word")

    def g_story_flag(self):
        i = self._free_word("story", 127)
        try:
            self.a.set_live_word("story", i, 0x8000)
            wait_until(lambda: self.b.live_word("story", i) == 0x8000 and self.b.save_word("story", i) == 0x8000, 4)
            self.a.set_live_word("story", i, 0)
            wait_until(lambda: self.b.live_word("story", i) == 0 and self.b.save_word("story", i) == 0, 4)
            return f"story[{i}] bit 15 set and cleared A->B (live and save copy)"
        finally:
            for g in (self.a, self.b):
                g.set_live_word("story", i, 0)
                g.set_save_word("story", i, 0)

    def g_key_and_chest(self):
        scene = self.a.side.scene_index
        assert scene == self.b.side.scene_index and scene is not None and scene < 16, f"scene index {scene}"
        key, chest = scene * 8 + 6, scene * 2 + 1
        orig = {(g.name, n, i): (g.live_word(n, i), g.save_word(n, i)) for g in (self.a, self.b)
                for n, i in (("dungeon", key), ("tbox", chest))}
        assert all(v == (0, 0) for v in orig.values()), f"test words not free: {orig}"
        try:
            self.a.set_live_word("dungeon", key, 0x8000)                  # key picked up in A
            wait_until(lambda: self.b.live_word("dungeon", key) == 0x8000 and self.b.save_word("dungeon", key) == 0x8000, 4)
            self.a.set_live_word("dungeon", key, 0)                       # key used in A
            wait_until(lambda: self.b.live_word("dungeon", key) == 0 and self.b.save_word("dungeon", key) == 0, 4)
            self.b.set_live_word("tbox", chest, 0x8000)                   # chest opened in B
            wait_until(lambda: self.a.live_word("tbox", chest) == 0x8000 and self.a.save_word("tbox", chest) == 0x8000, 4)
            return "key pickup and use A->B, chest B->A"
        finally:
            self._quiet_restore([("dungeon", key), ("tbox", chest)])

    def g_mass_clear_brake(self):
        words = []
        for _ in range(3):
            i = self._free_word("story", 127 if not words else words[-1] - 1)
            words.append(i)
        try:
            for i in words:
                self.a.set_live_word("story", i, 0x4000)
            wait_until(lambda: all(self.b.live_word("story", i) == 0x4000 for i in words), 4)
            for i in words:                                               # B's flags wiped at once
                self.b.set_live_word("story", i, 0)
            time.sleep(2.0)
            kept = [self.a.live_word("story", i) for i in words]
            assert all(v == 0x4000 for v in kept), f"A lost flags after B's wipe: {kept}"
            wait_until(lambda: all(self.b.live_word("story", i) == 0x4000 for i in words), 4)
            return f"wipe of story{words} in B did not clear A, B got them back"
        finally:
            self._quiet_restore([("story", i) for i in words])

    def _quiet_restore(self, words):
        """Put test words back to 0 in both games without the bridges carrying anything: stop the bridges' flag
        traffic by clearing both at once, then clearing the save copies."""
        for g in (self.a, self.b):
            for name, i in words:
                g.set_live_word(name, i, 0)
                g.set_save_word(name, i, 0)
        time.sleep(1.0)
        for g in (self.a, self.b):
            for name, i in words:
                assert g.live_word(name, i) == 0, f"cleanup: {g.name} {name}[{i}] is back"

    def g_outfit_respawn(self):
        link = self.a.link()
        tunic = self.a.d.read(link + LINK_TUNIC, 1)[0]
        other = 0 if tunic == 1 else 1
        heap_before = heap_free_work1(self.b)
        _, n0 = self.b.events()
        try:
            self.a.d.write(link + LINK_TUNIC, bytes([other]))
            wait_until(lambda: (lambda ev: 6 in ev[0][-4:] and ev[0][-1] == 1 and ev[1] > n0)(self.b.events()), 8)
        finally:
            self.a.d.write(link + LINK_TUNIC, bytes([tunic]))
        _, n1 = self.b.events()
        wait_until(lambda: (lambda ev: ev[1] > n1 and ev[0][-1] == 1)(self.b.events()), 8)
        assert self.b.alive(), "B stopped running after the outfit change (hang regression)"
        wait_until(lambda: m.valid_ptr(self.b.puppet()), 5)
        heap_after = heap_free_work1(self.b)
        assert heap_after is not None and abs(heap_after - heap_before) < 64 * 1024, \
            f"work1Heap free changed {heap_before} -> {heap_after}"
        return f"two respawns, B alive, work1Heap free {heap_before // 1024} -> {heap_after // 1024} KB"

    def g_disconnect_reconnect(self):
        self.stop_bridge("join")
        wait_until(lambda: self.a.mail_flags() == 1, 12)                # A: bridge only = puppet hidden
        self.start_bridge("join", self.b.pid)
        wait_until(lambda: self.a.mail_flags() & 7 == 7 and self.b.mail_flags() & 7 == 7, 15)
        return "A hid the puppet when the peer vanished, both reconnected"

    def g_invariants(self):
        ev0 = (self.a.events()[1], self.b.events()[1])
        worst = 0.0
        for _ in range(5):
            for mover, viewer in ((self.a, self.b), (self.b, self.a)):
                p = viewer.puppet_pos()
                assert p is not None, f"{viewer.name} has no puppet"
                gap = m_dist(p, mover.link_pos())
                worst = max(worst, gap)
                assert gap < 25, f"{viewer.name}'s puppet {gap:.1f} units from {mover.name}'s Link"
            assert self.a.alive() and self.b.alive(), "a game stopped"
            time.sleep(1.0)
        assert (self.a.events()[1], self.b.events()[1]) == ev0, "puppet events happened during the monitor"
        return f"5 samples: worst puppet gap {worst:.1f} units, no respawns, both alive"

    # --- run ---
    def run(self):
        os.makedirs(OUT, exist_ok=True)
        saves_before = {p: sha(p) for p in OWNER_SAVES if os.path.exists(p)}
        self.gate("scorer self-test", self.g_scorer_selftest)
        for inst in ("A", "B"):
            state = os.path.join(TEST_DIRS[inst], "StateSaves", "SOUE01.s01")
            if not os.path.exists(state):
                self.results.append({"gate": "savestates", "status": "FAIL", "detail": f"missing {state}"})
                return self.finish(saves_before)
        try:
            self._body()
        finally:
            for role in list(self.bridges):
                self.stop_bridge(role)
            if not self.args.keep_open:
                self.close_games()
        return self.finish(saves_before)

    def _body(self):
        if True:
            pa, _ = self.launch("A")
            time.sleep(2)
            pb, _ = self.launch("B")
            def started():
                wait_until(lambda: _try(lambda: GameSide(pa)) and _try(lambda: GameSide(pb)), 90, 1.0)
                return "both games run the co-op hook"
            if not self.gate("games start", started):
                return
            time.sleep(3)
            self.a, self.b = Game("A", pa), Game("B", pb)
            if not self.gate("boot from savestates", self.g_boot):
                return
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
            s.close()
            self.start_bridge("host", pa)
            time.sleep(0.5)
            self.start_bridge("join", pb)
            if self.gate("bridges connect", self.g_connect):
                self.gate("position A->B", self.g_position_a_to_b)
                self.gate("position B->A", self.g_position_b_to_a)
                self.gate("facing + animation", self.g_facing_and_anim)
                self.gate("equipment", self.g_equipment)
                self.gate("held items", self.g_held_items)
                self.gate("story flag set/clear", self.g_story_flag)
                self.gate("small key + chest", self.g_key_and_chest)
                self.gate("mass-clear brake", self.g_mass_clear_brake)
                self.gate("enemy kill", self.g_enemy_kill)
                self.gate("enemy motion", self.g_enemy_motion)
                self.gate("enemy animation", self.g_enemy_animation)
                self.gate("outfit respawn (hang)", self.g_outfit_respawn)
                self.gate("disconnect + reconnect", self.g_disconnect_reconnect)
                self.gate("invariant monitor", self.g_invariants)

    def close_games(self):
        for pid in self.procs:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(1)
        alive = [pid for pid in self.procs if _try(lambda: m.Dolphin(pid=pid))]
        self.results.append({"gate": "games closed", "status": "PASS" if not alive else "FAIL",
                             "detail": f"still running: {alive}" if alive else "all test Dolphins exited"})
        r = self.results[-1]
        print(f"{r['status']:7} {r['gate']:28} {r['detail']}", flush=True)

    def finish(self, saves_before):
        changed = [p for p, h in saves_before.items() if sha(p) != h]
        self.results.append({"gate": "owner saves untouched", "status": "FAIL" if changed else "PASS",
                             "detail": f"changed: {changed}" if changed else f"{len(saves_before)} save(s) hash-equal"})
        for r in self.results[-1:]:
            print(f"{r['status']:7} {r['gate']:28} {r['detail']}", flush=True)
        verdict = score(self.results)
        with open(os.path.join(OUT, "result.json"), "w") as f:
            json.dump({"result": verdict, "mutant": self.args.mutant, "gates": self.results}, f, indent=2)
        print(f"RESULT: {verdict}", flush=True)
        return 0 if verdict == "PASS" else 1


def score(results):
    return "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL"


def _r(p):
    return tuple(round(v, 1) for v in p) if p else None


def m_dist(p, q):
    return sum((a - b) ** 2 for a, b in zip(p, q)) ** 0.5


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _try(fn):
    try:
        return fn()
    except (SystemExit, Exception):
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-build", type=int, default=19)
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--mutant", default="")
    sys.exit(Run(ap.parse_args()).run())
