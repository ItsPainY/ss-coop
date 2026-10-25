"""Tests for enemy_motion.py and the OWN/MOTION messages, without the game.

  python -I test_enemy_motion.py

Includes the playbook's stale-assignment checks (coop-anything/playbook/actor-ownership.md) and their mutation twin:
with the epoch check switched off (mutant "no_epoch") those tests must fail, which test_mutant_is_caught checks.
"""

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enemy_motion as em  # noqa: E402
import net_bridge as nb  # noqa: E402


class FakeGame:
    """One game: a stage, its enemies, and a puppet that applies orders like updateEnemies does."""

    def __init__(self, stage="D100"):
        self.stage = stage
        self.enemies = {}        # objId -> dict(pos, rot, health, profile)
        self.orders = []
        self.seen = {}
        self.link = (0.0, 0.0, 0.0)

    def add(self, oid, pos, health=8):
        self.enemies[oid] = dict(pos=tuple(pos), rot=0, health=health, profile=0x171, anim=0, mode=0, frame=0.0)

    # GameSide interface
    def enemy_stage(self):
        return self.stage

    def enemy_table(self):
        return [dict(objId=o, profile=e["profile"], pos=e["pos"], rot=e["rot"], health=e["health"], anim=e["anim"],
                     mode=e["mode"], frame=e["frame"]) for o, e in sorted(self.enemies.items())]

    def write_enemy_orders(self, orders):
        self.orders = list(orders)
        for oid, rot, pos, cap, flags, (anim, mode, seq, frame) in self.orders:   # the puppet, one frame later
            e = self.enemies.get(oid)
            if e is None:
                continue
            if flags & em.ORDER_FOLLOW:
                e["pos"], e["rot"] = tuple(pos), rot
            e["health"] = min(e["health"], cap)
            if flags & em.ORDER_ANIM:
                if e["anim"] != anim:
                    e["anim"], e["mode"], e["frame"] = anim, mode, frame
                elif self.seen.get(oid) != seq:
                    e["frame"] = frame
                self.seen[oid] = seq

    def following(self, oid):
        return any(o[0] == oid and o[4] & em.ORDER_FOLLOW for o in self.orders)


class Pair:
    """Host and joiner wired directly (no network), with a controllable clock."""

    def __init__(self, mutant=""):
        self.a, self.b = FakeGame(), FakeGame()
        self.ha, self.hb = em.EnemyMotion(self.a, "host", mutant), em.EnemyMotion(self.b, "join", mutant)
        self.t = 0.0
        self.log = []            # every message the host sent, for replay tests

    def step(self, dt=0.05):
        self.t += dt
        for src, dst, game, other in ((self.ha, self.hb, self.a, self.b), (self.hb, self.ha, self.b, self.a)):
            for kind, stage, entries in src.tick(self.t, game.link, other.link, other.stage):
                if src is self.ha:
                    self.log.append((kind, stage, list(entries)))
                (dst.on_own if kind == "own" else dst.on_motion)(stage, entries, self.t)

    def run(self, seconds, dt=0.05):
        for _ in range(int(seconds / dt)):
            self.step(dt)


def settled_pair(mutant=""):
    p = Pair(mutant)
    p.a.add(1, (100, 0, 0))
    p.b.add(1, (100, 0, 0))
    p.a.link, p.b.link = (0, 0, 0), (1000, 0, 0)     # host is nearer
    p.run(em.SETTLE + 0.5)
    return p


class Ownership(unittest.TestCase):
    def test_nearer_player_owns_and_other_follows(self):
        p = settled_pair()
        self.assertEqual(p.ha.owners[1][0], em.HOST)
        self.assertEqual(p.hb.owners[1], p.ha.owners[1])
        p.a.enemies[1]["pos"] = (150, 0, 20)          # host's enemy walks
        p.a.enemies[1]["rot"] = 1234
        p.run(0.3)
        self.assertEqual(p.b.enemies[1]["pos"], (150, 0, 20))
        self.assertEqual(p.b.enemies[1]["rot"], 1234)
        self.assertFalse(p.a.following(1))

    def test_handoff_bumps_epoch_and_reverses_following(self):
        p = settled_pair()
        ep0 = p.ha.owners[1][1]
        p.a.link, p.b.link = (1000, 0, 0), (100, 0, 10)   # joiner walks up to the enemy
        p.run(em.HOLD + 0.5)
        owner, ep1 = p.ha.owners[1]
        self.assertEqual(owner, em.JOIN)
        self.assertGreater(ep1, ep0)
        self.assertEqual(p.hb.owners[1], (em.JOIN, ep1))
        p.b.enemies[1]["pos"] = (90, 0, 5)
        p.run(0.3)
        self.assertEqual(p.a.enemies[1]["pos"], (90, 0, 5))
        self.assertTrue(p.a.following(1))
        self.assertFalse(p.b.following(1))

    def test_no_flapping_between_equal_players(self):
        p = settled_pair()
        p.a.link, p.b.link = (0, 0, 0), (200, 0, 0)     # enemy at 100: equally far
        changes, last = 0, p.ha.owners[1]
        for k in range(60):                              # 3 s of jitter around equal distance
            p.a.link = (0 + (5 if k % 2 else -5), 0, 0)
            p.step()
            if p.ha.owners[1] != last:
                changes, last = changes + 1, p.ha.owners[1]
        self.assertEqual(changes, 0)

    def test_hold_time(self):
        p = settled_pair()
        p.a.link, p.b.link = (1000, 0, 0), (100, 0, 0)
        p.step()
        self.assertEqual(p.ha.owners[1][0], em.HOST)     # held: owner got it less than HOLD ago? (settle >= HOLD)
        p2 = Pair()
        p2.a.add(1, (100, 0, 0)); p2.b.add(1, (100, 0, 0))
        p2.a.link, p2.b.link = (0, 0, 0), (1000, 0, 0)
        p2.run(em.SETTLE + 0.1)
        p2.a.link, p2.b.link = (1000, 0, 0), (100, 0, 0)
        p2.run(em.HOLD / 2)
        self.assertEqual(p2.ha.owners[1][0], em.HOST)
        p2.run(em.HOLD)
        self.assertEqual(p2.ha.owners[1][0], em.JOIN)


class Stale(unittest.TestCase):
    def test_replayed_old_assignment_does_not_win(self):
        p = settled_pair()
        old_own = [m for m in p.log if m[0] == "own"][-1]
        p.a.link, p.b.link = (1000, 0, 0), (100, 0, 0)
        p.run(em.HOLD + 0.5)
        now_owner = p.hb.owners[1]
        p.hb.on_own(old_own[1], old_own[2], p.t)          # the old grant arrives again (late duplicate)
        self.assertEqual(p.hb.owners[1], now_owner)

    def test_past_owner_motion_is_dropped(self):
        p = settled_pair()
        _, ep_host = p.ha.owners[1]
        p.a.link, p.b.link = (1000, 0, 0), (100, 0, 0)
        p.run(em.HOLD + 0.5)                               # joiner owns now
        p.a.link, p.b.link = (100, 0, 0), (1000, 0, 0)
        p.run(em.HOLD + 0.5)                               # host owns again, newer epoch
        owner, ep_now = p.hb.owners[1]
        self.assertEqual(owner, em.HOST)
        p.b.enemies[1]["pos"] = (5, 5, 5)
        p.ha.on_motion("D100", [(1, ep_now - 1, (777, 0, 0), 0, 8)], p.t)  # late motion from the joiner's turn
        p.ha.on_motion("D100", [(1, ep_now - 1, (777, 0, 0), 0, 8)], p.t)
        self.assertNotEqual(p.ha.peer.get(1, {}).get("pos"), (777, 0, 0))
        p.hb.on_motion("D100", [(1, ep_host, (666, 0, 0), 0, 8)], p.t)     # host's motion from its first turn
        self.assertNotEqual(p.hb.peer[1].get("pos"), (666, 0, 0))

    def test_mutant_is_caught(self):
        """The two stale tests must fail without the epoch check (mutation twin)."""
        p = settled_pair("no_epoch")
        old_own = [m for m in p.log if m[0] == "own"][-1]
        p.a.link, p.b.link = (1000, 0, 0), (100, 0, 0)
        p.run(em.HOLD + 0.5)
        now_owner = p.hb.owners[1]
        p.hb.on_own(old_own[1], old_own[2], p.t)
        self.assertNotEqual(p.hb.owners[1], now_owner, "mutant not caught: old assignment should win without epochs")


class Animation(unittest.TestCase):
    def test_owner_animation_and_frame_reach_the_follower(self):
        p = settled_pair()
        p.a.enemies[1].update(anim=3, mode=1, frame=12.5)      # host's enemy starts an attack animation
        p.run(0.3)
        self.assertEqual((p.b.enemies[1]["anim"], p.b.enemies[1]["mode"], p.b.enemies[1]["frame"]), (3, 1, 12.5))
        p.b.enemies[1]["frame"] = 13.5                           # the follower plays on by itself...
        p.run(0.02)
        self.assertEqual(p.b.enemies[1]["frame"], 13.5)          # ...until a new owner frame arrives
        p.a.enemies[1]["frame"] = 20.0
        p.run(0.3)
        self.assertEqual(p.b.enemies[1]["frame"], 20.0)

    def test_frame_seq_changes_only_on_new_motion(self):
        p = settled_pair()
        seqs = []
        for _ in range(10):
            p.step(0.01)                                         # faster than MOTION_EVERY
            o = next(o for o in p.b.orders if o[0] == 1)
            seqs.append(o[5][2])
        self.assertLess(len(set(seqs)), len(seqs))               # same seq repeated between motions

    def test_unknown_type_sends_no_anim_order(self):
        p = settled_pair()
        p.a.enemies[1]["anim"] = em.NO_ANIM
        p.run(0.3)
        o = next(o for o in p.b.orders if o[0] == 1)
        self.assertFalse(o[4] & em.ORDER_ANIM)

    def test_mutant_without_anim_is_caught(self):
        p = settled_pair("no_enemy_anim")
        p.a.enemies[1].update(anim=3, mode=1, frame=12.5)
        p.run(0.3)
        self.assertNotEqual(p.b.enemies[1]["anim"], 3, "mutant not caught")


class Health(unittest.TestCase):
    def test_lower_health_wins_both_ways(self):
        p = settled_pair()
        p.b.enemies[1]["health"] = 5                       # joiner hits the host-owned enemy
        p.run(0.3)
        self.assertEqual(p.a.enemies[1]["health"], 5)
        p.a.enemies[1]["health"] = 2                       # host hits too
        p.run(0.3)
        self.assertEqual(p.b.enemies[1]["health"], 2)

    def test_no_order_without_need(self):
        p = settled_pair()
        self.assertEqual(p.a.orders, [])                    # host owns, same health: nothing to do


class Stages(unittest.TestCase):
    def test_other_stage_no_following(self):
        p = Pair()
        p.a.add(1, (100, 0, 0)); p.b.add(1, (100, 0, 0))
        p.b.stage = "F000"
        p.run(em.SETTLE + 1)
        p.a.enemies[1]["pos"] = (300, 0, 0)
        p.run(0.5)
        self.assertEqual(p.b.enemies[1]["pos"], (100, 0, 0))
        self.assertEqual(p.hb.owners, {})

    def test_area_change_resets_view(self):
        p = settled_pair()
        p.b.stage = "F000"
        p.step()
        self.assertEqual(p.hb.owners, {})
        self.assertEqual(p.hb.peer, {})

    def test_enemy_only_in_one_game(self):
        p = Pair()
        p.a.add(1, (100, 0, 0))                            # joiner's copy is already dead
        p.run(em.SETTLE + 1)
        self.assertEqual(p.b.orders, [])


class Wire(unittest.TestCase):
    def test_roundtrip(self):
        own = ("D100", [(1, 0, 7), (1027, 1, 8)])
        self.assertEqual(nb.decode(nb.encode(nb.OWN_T, 2, own)), (nb.OWN_T, 2, own))
        mot = ("D100", [(1, 7, (1.5, -2.0, 3.25), -100, 6, 0x8003, 1, 12.5)])
        self.assertEqual(nb.decode(nb.encode(nb.MOTION_T, 2, mot)), (nb.MOTION_T, 2, mot))

    def test_full_table_fits(self):
        mot = ("D100", [(i, 1, (0.0, 0.0, 0.0), 0, 1, 0, 0, 0.0) for i in range(em.MAX_ENEMIES)])
        self.assertLessEqual(len(nb.encode(nb.MOTION_T, 1, mot)), 2048)


class Side(FakeGame):
    def __init__(self, link):
        super().__init__()
        self.link = link
        self.state = ("D100", 0, link, 0)

    def link_state(self):
        self.state = ("D100", 0, self.link, 0)
        return self.state

    def write_mail(self, *a, **k):
        pass


class EndToEnd(unittest.TestCase):
    def test_two_bridges_over_udp(self):
        a, b = Side((0.0, 0.0, 0.0)), Side((1000.0, 0.0, 0.0))
        a.add(1, (100, 0, 0)); b.add(1, (100, 0, 0))
        hs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); hs.bind(("127.0.0.1", 0))
        js = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); js.bind(("127.0.0.1", 0))
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        join = nb.NetBridge(b, js, "join", peer=hs.getsockname(), log=lambda s: None)
        try:
            def run(seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    now = time.monotonic()
                    host.tick(now)
                    join.tick(now)
                    time.sleep(1 / 120)
            run(em.SETTLE + 0.5)
            a.enemies[1]["pos"] = (120, 0, 40)
            run(0.5)
            self.assertEqual(b.enemies[1]["pos"], (120.0, 0.0, 40.0))
        finally:
            host.close()
            join.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
