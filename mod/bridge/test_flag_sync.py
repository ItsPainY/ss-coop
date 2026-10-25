"""Flag sync tests without the game: a fake game with live arrays, a save copy and commits, over real UDP.

  python -I test_flag_sync.py            (unit + loopback)
  python -I test_flag_sync.py --chaos    (also through coop-anything/tools/chaos, profile bad_wifi)
"""

import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flag_sync as fs  # noqa: E402
import net_bridge as nb  # noqa: E402
from test_net_bridge import udp, run, CHAOS  # noqa: E402

SIZES = {"story": 128, "item": 64, "scene": 208, "dungeon": 176, "tbox": 32}
PER_SCENE = {"scene": 8, "dungeon": 8, "tbox": 2}  # scene-indexed arrays: words per scene entry


class FakeFlagGame:
    """Models the parts of the game the sync relies on.

    story/item: live array, copied over the save copy on commit().
    scene/dungeon/tbox: save copy indexed by scene; a live mirror holds the current scene's entry, is copied back
    on commit() and loaded from the save on enter_scene(). While loading (no Link) flag_view() is None.
    """

    def __init__(self, scene_idx=0):
        self.live = {"story": [0] * 128, "item": [0] * 64}
        self.save = {k: [0] * n for k, n in SIZES.items()}
        self.scene_idx = scene_idx
        self.mirror = {k: list(self._entry(k, scene_idx)) for k in PER_SCENE}
        self.loading = False
        self.state = ("F000", 0, (0.0, 0.0, 0.0), 0)
        self.mail = None

    def _entry(self, name, idx):
        per = PER_SCENE[name]
        return self.save[name][idx * per:(idx + 1) * per]

    @property
    def scene_live(self):
        return self.mirror["scene"]

    @property
    def scene_index(self):  # read by FlagSync, like GameSide.scene_index
        return self.scene_idx

    # game behaviour
    def set_flag(self, name, idx, bit):
        if name in PER_SCENE:
            self.mirror[name][idx % PER_SCENE[name]] |= 1 << bit
        else:
            self.live[name][idx] |= 1 << bit

    def clear_flag(self, name, idx, bit):
        if name in PER_SCENE:
            self.mirror[name][idx % PER_SCENE[name]] &= ~(1 << bit)
        else:
            self.live[name][idx] &= ~(1 << bit)

    def commit(self):
        for k in ("story", "item"):
            self.save[k] = list(self.live[k])
        for k, per in PER_SCENE.items():
            if (self.scene_idx + 1) * per <= SIZES[k]:
                self.save[k][self.scene_idx * per:(self.scene_idx + 1) * per] = self.mirror[k]

    def enter_scene(self, idx):
        self.commit()
        self.scene_idx = idx
        self.mirror = {k: list(self._entry(k, idx)) for k in PER_SCENE}

    def word(self, name, idx):
        """What the game would read for this logical word right now."""
        if name in PER_SCENE and idx // PER_SCENE[name] == self.scene_idx:
            return self.mirror[name][idx % PER_SCENE[name]]
        return self.live[name][idx] if name in self.live else self.save[name][idx]

    # GameSide interface
    def link_state(self):
        return None if self.loading else self.state

    def write_mail(self, flags, pos=(0.0, 0.0, 0.0), rot_y=0, tunic=None, anim=None, equip=None, held=None):
        self.mail = (flags, pos, rot_y)

    def flag_view(self):
        if self.loading:
            return None
        view = {"story": list(self.live["story"]), "item": list(self.live["item"])}
        for k, per in PER_SCENE.items():
            view[k] = list(self.save[k])
            if (self.scene_idx + 1) * per <= SIZES[k]:
                view[k][self.scene_idx * per:(self.scene_idx + 1) * per] = self.mirror[k]
        return view

    def flag_write(self, name, idx, value):
        self.save[name][idx] = value
        if name in self.live:
            self.live[name][idx] = value
        elif idx // PER_SCENE[name] == self.scene_idx:
            self.mirror[name][idx % PER_SCENE[name]] = value


def bridges(a, b, host_target=None):
    hs = udp()
    host = nb.NetBridge(a, hs, "host", log=lambda s: None)
    join = nb.NetBridge(b, udp(), "join", peer=host_target or hs.getsockname(), log=lambda s: None)
    return host, join


def settle(bs, seconds=0.6):
    run(bs, seconds)


class Core(unittest.TestCase):
    def test_merge_set_then_clear(self):
        self.assertEqual(fs.merge((0b01, 0), (0, 0b01)), (0, 0b01))
        self.assertEqual(fs.merge((0, 0b01), (0b01, 0)), (0b01, 0))
        self.assertEqual(fs.merge((0b01, 0), (0b10, 0)), (0b11, 0))

    def test_queue_merges_and_drops_noops(self):
        q = fs.ChangeQueue()
        q.add("story", 3, 0b1, 0)
        q.add("story", 3, 0, 0b1)
        q.add("story", 4, 0b10, 0)
        self.assertEqual(q.take(10), [("story", 3, 0, 1), ("story", 4, 2, 0)])

    def test_poll_and_no_echo(self):
        g = FakeFlagGame()
        s = fs.FlagSync(g)
        self.assertEqual(s.poll(), [])                      # first poll = baseline
        g.set_flag("story", 5, 3)
        self.assertEqual(s.poll(), [("story", 5, 8, 0)])
        s.apply([("item", 2, 1, 0)])                         # remote change
        self.assertEqual(g.live["item"][2], 1)
        self.assertEqual(s.poll(), [])                      # not sent back

    def test_apply_keeps_unsent_local_bits(self):
        g = FakeFlagGame()
        s = fs.FlagSync(g)
        s.poll()
        g.set_flag("story", 1, 0)                            # local, not polled yet
        s.apply([("story", 1, 0b10, 0)])                     # remote sets another bit of the same word
        self.assertEqual(g.live["story"][1], 0b11)
        self.assertEqual(s.poll(), [("story", 1, 0b01, 0)])  # local bit still goes out

    def test_scene_clears_not_sent(self):
        g = FakeFlagGame()
        s = fs.FlagSync(g)
        s.poll()
        g.scene_live[0] = 0b1
        self.assertEqual(s.poll(), [("scene", 0, 1, 0)])
        g.scene_live[0] = 0
        self.assertEqual(s.poll(), [])

    def test_not_ready_while_loading(self):
        g = FakeFlagGame()
        s = fs.FlagSync(g)
        g.loading = True
        self.assertFalse(s.apply([("story", 0, 1, 0)]))
        self.assertEqual(g.live["story"][0], 0)


class OverUdp(unittest.TestCase):
    def test_story_item_both_ways(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("story", 6, 3)
        b.set_flag("item", 10, 0)
        settle([host, join])
        self.assertEqual(b.live["story"][6], 8)
        self.assertEqual(b.save["story"][6], 8)              # survives the next commit / save
        self.assertEqual(a.live["item"][10], 1)
        b.commit()
        self.assertEqual(b.save["story"][6], 8)

    def test_clear_travels_for_story(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("story", 2, 1)
        settle([host, join])
        a.clear_flag("story", 2, 1)
        settle([host, join])
        self.assertEqual(b.live["story"][2], 0)

    def test_same_word_different_bits(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("story", 9, 0)
        b.set_flag("story", 9, 5)
        settle([host, join])
        self.assertEqual(a.live["story"][9], 0b100001)
        self.assertEqual(b.live["story"][9], 0b100001)

    def test_scene_in_other_area_lands_on_entry(self):
        a, b = FakeFlagGame(scene_idx=3), FakeFlagGame(scene_idx=7)
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("scene", 3 * 8 + 2, 4)                    # a opens something in scene 3
        settle([host, join])
        self.assertEqual(b.save["scene"][3 * 8 + 2], 16)     # b is elsewhere: save copy only
        b.commit()                                           # b's commit must not wipe it (other scene)
        b.enter_scene(3)
        self.assertEqual(b.word("scene", 3 * 8 + 2), 16)

    def test_scene_in_same_area_goes_live(self):
        a, b = FakeFlagGame(scene_idx=4), FakeFlagGame(scene_idx=4)
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("scene", 4 * 8 + 1, 2)
        settle([host, join])
        self.assertEqual(b.scene_live[1], 4)
        b.commit()
        self.assertEqual(b.save["scene"][4 * 8 + 1], 4)

    def test_snapshot_on_connect_is_union(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        a.live["story"][0], b.live["story"][0], b.live["item"][3] = 0b01, 0b10, 7
        host, join = bridges(a, b)
        settle([host, join])
        for g in (a, b):
            self.assertEqual(g.live["story"][0], 0b11)
            self.assertEqual(g.live["item"][3], 7)

    def test_loading_peer_gets_it_after_load(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        b.loading = True
        a.set_flag("story", 20, 7)
        settle([host, join])
        self.assertEqual(b.live["story"][20], 0)              # not applied mid-load
        b.loading = False
        settle([host, join])
        self.assertEqual(b.live["story"][20], 128)

    def test_reloading_older_save_does_not_erase_peer(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        old_b = {k: list(v) for k, v in b.live.items()}       # b's older save, before the new progress
        a.set_flag("story", 40, 1)
        a.set_flag("item", 5, 2)
        settle([host, join])
        self.assertEqual(b.live["story"][40], 2)
        b.loading = True                                      # b loads the older save
        settle([host, join], 0.3)
        b.live = {k: list(v) for k, v in old_b.items()}
        b.save["story"], b.save["item"] = list(old_b["story"]), list(old_b["item"])
        b.loading = False
        settle([host, join], 1.0)
        self.assertEqual(a.live["story"][40], 2)              # not cleared on the host
        self.assertEqual(a.live["item"][5], 4)
        self.assertEqual(b.live["story"][40], 2)              # and restored on b
        self.assertEqual(b.live["item"][5], 4)

    def test_wipe_without_load_does_not_erase_peer(self):
        # Seen in Dolphin: during a reset the flag arrays were zeroed while LINK still looked valid.
        a, b = FakeFlagGame(), FakeFlagGame()
        for i in (1, 6, 11, 45):
            a.set_flag("story", i, 3)
        host, join = bridges(a, b)
        settle([host, join])
        self.assertEqual(b.live["story"][6], 8)
        b.live = {"story": [0] * 128, "item": [0] * 64}      # wiped, no loading phase in between
        settle([host, join], 1.0)
        for i in (1, 6, 11, 45):
            self.assertEqual(a.live["story"][i], 8, i)        # host keeps its progress
            self.assertEqual(b.live["story"][i], 8, i)        # and b gets it back

    def test_single_real_clear_still_travels(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        for i in (1, 6, 11, 45):
            a.set_flag("story", i, 3)
        host, join = bridges(a, b)
        settle([host, join])
        b.clear_flag("story", 11, 3)                          # one flag cleared in play: a real clear
        settle([host, join])
        self.assertEqual(a.live["story"][11], 0)

    def test_small_key_pickup_and_use(self):
        # Both in dungeon scene 5. The small key counter lives in the dungeon flags (word 7 here, low bits).
        a, b = FakeFlagGame(scene_idx=5), FakeFlagGame(scene_idx=5)
        host, join = bridges(a, b)
        settle([host, join])
        key = 5 * 8 + 7
        a.set_flag("dungeon", key, 0)                         # a picks up a key: counter 0 -> 1
        settle([host, join])
        self.assertEqual(b.word("dungeon", key), 1)
        a.clear_flag("dungeon", key, 0)                       # a uses it on a door: counter 1 -> 0
        a.set_flag("scene", 5 * 8 + 3, 6)                     # and the door's scene flag is set
        settle([host, join])
        self.assertEqual(b.word("dungeon", key), 0)           # b does not keep a spare key
        self.assertEqual(b.word("scene", 5 * 8 + 3), 64)      # and sees the door open
        b.commit()
        self.assertEqual(b.save["dungeon"][key], 0)

    def test_stale_dungeon_word_on_scene_change_is_not_a_clear(self):
        a, b = FakeFlagGame(scene_idx=5), FakeFlagGame(scene_idx=5)
        host, join = bridges(a, b)
        a.set_flag("dungeon", 5 * 8 + 7, 0)
        settle([host, join])
        self.assertEqual(b.word("dungeon", 5 * 8 + 7), 1)
        # a leaves scene 5 but its save copy is behind (commit not yet done): entry 5 now reads 0 from the save.
        a.save["dungeon"][5 * 8 + 7] = 0
        a.scene_idx = 6
        a.mirror = {k: list(a._entry(k, 6)) for k in PER_SCENE}
        settle([host, join])
        self.assertEqual(b.word("dungeon", 5 * 8 + 7), 1)     # b keeps its key

    def test_chest_reaches_player_in_other_scene(self):
        a, b = FakeFlagGame(scene_idx=3), FakeFlagGame(scene_idx=9)
        host, join = bridges(a, b)
        settle([host, join])
        a.set_flag("tbox", 3 * 2 + 1, 4)                      # a opens a chest in scene 3
        settle([host, join])
        self.assertEqual(b.save["tbox"][3 * 2 + 1], 16)
        b.enter_scene(3)
        self.assertEqual(b.word("tbox", 3 * 2 + 1), 16)       # still closed? no: it's open for b too

    def test_quiet_when_converged(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        a.set_flag("story", 1, 1)
        settle([host, join])
        before = (host.stats["flags_out"], join.stats["flags_out"])
        settle([host, join], 0.5)
        self.assertEqual((host.stats["flags_out"], join.stats["flags_out"]), before)  # no echo ping-pong

    def test_rejoin_resyncs(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        host, join = bridges(a, b)
        settle([host, join])
        join.close()
        settle([host], 0.1)
        a.set_flag("story", 30, 2)                            # while nobody is connected
        c = FakeFlagGame()
        join2 = nb.NetBridge(c, udp(), "join", peer=host.sock.getsockname(), log=lambda s: None)
        settle([host, join2])
        self.assertEqual(c.live["story"][30], 4)


@unittest.skipUnless("--chaos" in sys.argv, "pass --chaos")
class ThroughChaos(unittest.TestCase):
    def test_random_changes_converge_under_bad_wifi(self):
        a, b = FakeFlagGame(), FakeFlagGame()
        hs = udp()
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        info = os.path.join(tempfile.mkdtemp(), "info.json")
        relay = subprocess.Popen([sys.executable, "-I", CHAOS, "relay", "--target", "127.0.0.1:%d" % hs.getsockname()[1],
                                  "--profile", "bad_wifi", "--info-file", info, "--seconds", "30"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(100):
                if os.path.exists(info) and os.path.getsize(info):
                    break
                time.sleep(0.05)
            with open(info) as f:
                listen = json.load(f)["listen"]
            join = nb.NetBridge(b, udp(), "join", peer=("127.0.0.1", listen), log=lambda s: None)
            rng = random.Random(7)
            t0 = time.monotonic()

            def churn(now):
                if now - t0 < 6.0 and rng.random() < 0.2:
                    g = rng.choice((a, b))
                    name = rng.choice(("story", "item"))
                    idx, bit = rng.randrange(SIZES[name]), rng.randrange(16)
                    if rng.random() < 0.8:
                        g.set_flag(name, idx, bit)
                    else:
                        g.clear_flag(name, idx, bit)

            run([host, join], 12.0, before=churn)
            print(f"\n  flags bad_wifi: host out {host.stats['flags_out']} in {host.stats['flags_in']} resends "
                  f"{host.stats['resends']} | join out {join.stats['flags_out']} in {join.stats['flags_in']} "
                  f"resends {join.stats['resends']}")
            for name in ("story", "item"):
                self.assertEqual(a.live[name], b.live[name], name)
            self.assertIsNone(host.inflight)
            self.assertIsNone(join.inflight)
        finally:
            relay.terminate()
            relay.communicate()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
