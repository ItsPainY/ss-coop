"""Tests for enemy_sync.py and the ENEMY message, without the game.

  python -I test_enemy_sync.py
"""

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enemy_sync as es  # noqa: E402
import net_bridge as nb  # noqa: E402


class FakeEnemyGame:
    """Stage name, 4096 enemy words, and a puppet that takes kill batches when told to."""

    def __init__(self, stage="D100"):
        self.stage = stage
        self.words = [0] * 0x1000
        self.writes = 0
        self.batches = []       # kill batches handed to the puppet
        self.puppet_busy = False
        self.state = (stage, 0, (0.0, 0.0, 0.0), 0)

    def kill(self, flag):
        self.words[flag // 16] |= 1 << (flag % 16)

    def has(self, flag):
        return bool(self.words[flag // 16] & (1 << (flag % 16)))

    def enter(self, stage):
        """Area change: the game wipes its enemy bits."""
        self.stage = stage
        self.words = [0] * 0x1000
        self.state = (stage, 0, (0.0, 0.0, 0.0), 0)

    # GameSide interface
    def enemy_stage(self):
        return self.stage

    def enemy_words(self):
        return None if self.stage is None else list(self.words)

    def enemy_write(self, i, v):
        self.words[i] = v
        self.writes += 1

    def kills_taken(self):
        return not self.puppet_busy

    def write_kills(self, ids):
        self.batches.append(list(ids))
        self.puppet_busy = True

    def link_state(self):
        return None if self.stage is None else self.state

    def write_mail(self, flags, pos=(0.0, 0.0, 0.0), rot_y=0, tunic=None, anim=None, equip=None, held=None):
        pass


def settle(*syncs, t=0.0):
    """Let each sync see its stage, then pass SETTLE."""
    for s in syncs:
        s.poll(t)
    return t + es.SETTLE + 0.01


class Core(unittest.TestCase):
    def test_kill_reaches_peer_and_its_puppet(self):
        a, b = FakeEnemyGame(), FakeEnemyGame()
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        a.kill(1029)  # room 1, placement 5
        out = sa.poll(t)
        self.assertEqual(out, ("D100", [(1029 // 16, 1 << (1029 % 16))]))
        self.assertEqual(sb.apply(*out, t), 1)
        self.assertTrue(b.has(1029))
        self.assertEqual(b.batches, [[1029]])

    def test_other_stage_is_ignored(self):
        a, b = FakeEnemyGame("D100"), FakeEnemyGame("F000")
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        a.kill(7)
        self.assertEqual(sb.apply(*sa.poll(t), t), 0)
        self.assertFalse(b.has(7))
        self.assertEqual(b.batches, [])

    def test_not_applied_right_after_entering(self):
        a, b = FakeEnemyGame(), FakeEnemyGame("F000")
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        a.kill(7)
        out = sa.poll(t)
        b.enter("D100")                          # B arrives in A's stage just now
        self.assertEqual(sb.apply(*out, t), 0)   # bits could still belong to B's previous visit timing: wait
        t2 = t + es.SETTLE + 0.01
        self.assertEqual(sb.apply(*out, t2), 1)

    def test_area_change_drops_pending_kills_and_old_bits(self):
        a, b = FakeEnemyGame(), FakeEnemyGame()
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        b.puppet_busy = True                     # puppet has not taken the last batch yet
        a.kill(3)
        out = sa.poll(t)
        sb.apply(*out, t)
        self.assertEqual(sb.pending_kills, [3])
        b.enter("F000")
        sb.poll(t + es.POLL_EVERY)
        self.assertEqual(sb.pending_kills, [])
        self.assertEqual(sb.apply(*out, t + 5), 0)   # A's D100 bits never land in F000
        self.assertFalse(b.has(3))

    def test_batches_wait_for_the_puppet(self):
        a, b = FakeEnemyGame(), FakeEnemyGame()
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        for f in range(20):
            a.kill(f)
        sb.apply(*sa.poll(t), t)
        self.assertEqual(len(b.batches), 1)
        self.assertEqual(len(b.batches[0]), es.KILL_SLOTS)
        sb.pump_kills()
        self.assertEqual(len(b.batches), 1)      # puppet still busy
        for _ in range(3):
            b.puppet_busy = False
            sb.pump_kills()
        self.assertEqual(sorted(f for batch in b.batches for f in batch), list(range(20)))

    def test_never_clears_and_no_echo_kills(self):
        a, b = FakeEnemyGame(), FakeEnemyGame()
        sa, sb = es.EnemySync(a), es.EnemySync(b)
        t = settle(sa, sb)
        a.kill(40)
        sb.apply(*sa.poll(t), t)
        writes = b.writes
        back = sb.poll(t + 1)                    # B sends A's kill back
        self.assertEqual(sa.apply(*back, t + 1), 0)
        a.words[40 // 16] = 0                    # something clears A's bit without an area change
        self.assertIsNone(sa.poll(t + 2))        # nothing left to send, and no "clear" message exists
        sb.poll(t + 2)
        self.assertTrue(b.has(40))
        self.assertEqual(b.writes, writes)

    def test_resends_without_changes(self):
        a = FakeEnemyGame()
        sa = es.EnemySync(a)
        t = settle(sa)
        a.kill(1)
        self.assertIsNotNone(sa.poll(t))
        self.assertIsNone(sa.poll(t + es.POLL_EVERY))            # nothing new, not yet time to resend
        self.assertIsNotNone(sa.poll(t + es.SEND_EVERY + 0.01))  # lost packets are covered by the resend

    def test_nothing_to_send(self):
        sa = es.EnemySync(FakeEnemyGame())
        t = settle(sa)
        self.assertIsNone(sa.poll(t))


class Wire(unittest.TestCase):
    def test_roundtrip(self):
        st = ("D100", [(0, 1), (64, 0x8001), (4095, 0xFFFF)])
        self.assertEqual(nb.decode(nb.encode(nb.ENEMY_T, 3, st)), (nb.ENEMY_T, 3, st))

    def test_bad_length(self):
        data = nb.encode(nb.ENEMY_T, 3, ("D100", [(1, 2)]))
        self.assertIsNone(nb.decode(data[:-1]))
        self.assertIsNone(nb.decode(data + b"\0"))

    def test_largest_fits_receive_buffer(self):
        data = nb.encode(nb.ENEMY_T, 1, ("D100", [(i, 1) for i in range(es.MAX_ENTRIES)]))
        self.assertLessEqual(len(data), 2048)


class EndToEnd(unittest.TestCase):
    def test_two_bridges_over_udp(self):
        a, b = FakeEnemyGame(), FakeEnemyGame()
        hs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hs.bind(("127.0.0.1", 0))
        js = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        js.bind(("127.0.0.1", 0))
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        join = nb.NetBridge(b, js, "join", peer=hs.getsockname(), log=lambda s: None)
        try:
            def run(seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    now = time.monotonic()
                    host.tick(now)
                    join.tick(now)
                    b.puppet_busy = False        # the puppet takes every batch at once
                    time.sleep(1 / 120)
            run(es.SETTLE + 0.3)
            a.kill(2050)
            b.kill(9)
            run(1.0)
            self.assertTrue(b.has(2050) and a.has(9))
            self.assertEqual(b.batches, [[2050]])
            self.assertEqual(a.batches, [[9]])
        finally:
            host.close()
            join.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
