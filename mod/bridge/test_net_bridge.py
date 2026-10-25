"""Tests for net_bridge.py without the game: fake game sides, real UDP on loopback, optional chaos relay.

  python -I test_net_bridge.py            (unit + loopback)
  python -I test_net_bridge.py --chaos    (also through coop-anything/tools/chaos, profile bad_wifi)
"""

import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import net_bridge as nb  # noqa: E402

CHAOS = os.path.join(HERE, "..", "..", "coop-anything", "tools", "chaos", "chaos.py")


class FakeSide:
    """Stands in for GameSide: a scripted Link and a record of mailbox writes."""

    def __init__(self, stage="F000", pos=(0.0, 0.0, 0.0), rot=0):
        self.state = (stage, 0, pos, rot)
        self.mail = None

    def link_state(self):
        return self.state

    def write_mail(self, flags, pos=(0.0, 0.0, 0.0), rot_y=0, tunic=None, anim=None, equip=None, held=None):
        self.mail = (flags, pos, rot_y)
        self.equip = equip


def udp(bind_port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", bind_port))
    return s


def pair(host_side, join_side, host_target=None):
    hs = udp()
    host = nb.NetBridge(host_side, hs, "host", log=lambda s: None)
    target = host_target or hs.getsockname()
    join = nb.NetBridge(join_side, udp(), "join", peer=target, log=lambda s: None)
    return host, join


def run(bridges, seconds, step=1 / 120, before=None):
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        now = time.monotonic()
        if before:
            before(now)
        for b in bridges:
            b.tick(now)
        time.sleep(step)


class Codec(unittest.TestCase):
    def test_roundtrip(self):
        equip = bytes(range(76))
        held = bytes([1, 2, 0xFF, 0xFF]) + bytes(range(100, 172))
        st = ("F001r", 1, (1.5, -2.25, 300.0), -16384, 1, (12, 13, 4.5, 7.25), equip, held)
        ptype, seq, back = nb.decode(nb.encode(nb.STATE_T, 7, st))
        self.assertEqual((ptype, seq), (nb.STATE_T, 7))
        self.assertEqual(back, st)

    def test_equipment_reaches_the_mailbox(self):
        a, b = FakeSide(), FakeSide()
        equip = bytes([5, 1, 3, 0]) + bytes(range(72))
        b.state = ("F000", 0, (0.0, 0.0, 0.0), 0, 0, nb.NO_ANIM, equip)
        host, join = pair(a, b)
        try:
            run([host, join], 0.5)
            self.assertEqual(a.equip, equip)
        finally:
            host.close(); join.close()

    def test_no_link(self):
        self.assertEqual(nb.decode(nb.encode(nb.STATE_T, 1, None)), (nb.STATE_T, 1, None))

    def test_room_above_127_survives_as_signed(self):
        self.assertEqual(nb.decode(nb.encode(nb.STATE_T, 1, ("F000", 200, (0, 0, 0), 0)))[2][1], 200 - 256)

    def test_garbage(self):
        for junk in (b"", b"SSC1", b"XXXX\x03\x00\x00\x00\x00\x01" + bytes(30), nb.encode(nb.STATE_T, 1, None)[:-3],
                     b"SSC1\x09\x00\x00\x00\x00\x01"):
            self.assertIsNone(nb.decode(junk), junk)

    def test_seq_wrap(self):
        self.assertTrue(nb.seq_newer(1, 0xFFFFFFFF))
        self.assertFalse(nb.seq_newer(0xFFFFFFFF, 1))
        self.assertFalse(nb.seq_newer(5, 5))


class Loopback(unittest.TestCase):
    def test_both_ways(self):
        a, b = FakeSide(pos=(10.0, 1.5, -20.0), rot=100), FakeSide(pos=(-5.0, 1.5, 7.0), rot=-200)
        host, join = pair(a, b)
        run([host, join], 0.5)
        self.assertTrue(host.connected and join.connected)
        self.assertEqual(a.mail, (nb.MAIL_BRIDGE | nb.MAIL_VISIBLE, (-5.0, 1.5, 7.0), -200))
        self.assertEqual(b.mail, (nb.MAIL_BRIDGE | nb.MAIL_VISIBLE, (10.0, 1.5, -20.0), 100))

    def test_moving_link_is_tracked(self):
        a, b = FakeSide(), FakeSide()
        host, join = pair(a, b)
        t0 = time.monotonic()

        def move(now):
            t = now - t0
            b.state = ("F000", 0, (100 * math.cos(t), 0.0, 100 * math.sin(t)), 0)

        run([host, join], 1.0, before=move)
        got, want = a.mail[1], b.state[2]
        self.assertLess(math.dist(got, want), 15.0)  # 30 Hz sends, ~100 units/s

    def test_different_stage_hides(self):
        a, b = FakeSide("F000"), FakeSide("F001r")
        host, join = pair(a, b)
        run([host, join], 0.4)
        self.assertEqual(a.mail[0], nb.MAIL_BRIDGE)
        self.assertEqual(b.mail[0], nb.MAIL_BRIDGE)

    def test_no_own_link_hides(self):
        a, b = FakeSide(), FakeSide()
        a.state = None  # loading screen on the host
        host, join = pair(a, b)
        run([host, join], 0.4)
        self.assertEqual(a.mail[0], nb.MAIL_BRIDGE)
        self.assertEqual(b.mail[0], nb.MAIL_BRIDGE)  # host sends has_link=0

    def test_timeout_hides(self):
        a, b = FakeSide(), FakeSide()
        host, join = pair(a, b)
        run([host, join], 0.3)
        self.assertEqual(a.mail[0], nb.MAIL_BRIDGE | nb.MAIL_VISIBLE)
        time.sleep(0.05)
        host.tick(time.monotonic())                          # drain what join already sent (else the jump
        host.tick(time.monotonic() + nb.PEER_TIMEOUT + 0.5)  # below stamps it "fresh"); join went silent
        self.assertEqual(a.mail[0], nb.MAIL_BRIDGE)

    def test_out_of_order_is_dropped(self):
        a = FakeSide()
        hs = udp()
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        c = udp()
        c.sendto(nb.encode(nb.HELLO, 1), hs.getsockname())
        c.sendto(nb.encode(nb.STATE_T, 10, ("F000", 0, (1.0, 0.0, 0.0), 0)), hs.getsockname())
        c.sendto(nb.encode(nb.STATE_T, 9, ("F000", 0, (9.0, 0.0, 0.0), 0)), hs.getsockname())
        time.sleep(0.05)
        host.tick(time.monotonic())
        self.assertEqual(a.mail[1], (1.0, 0.0, 0.0))
        self.assertEqual(host.stats["stale"], 1)

    def test_garbage_from_peer_is_ignored(self):
        a, b = FakeSide(), FakeSide(pos=(3.0, 0.0, 3.0))
        host, join = pair(a, b)
        run([host, join], 0.3)
        udp().sendto(b"hello there", host.sock.getsockname())
        host.sock.sendto(b"\x00" * 40, join.sock.getsockname())
        run([host, join], 0.2)
        self.assertEqual(a.mail[1], (3.0, 0.0, 3.0))
        self.assertGreaterEqual(host.stats["garbage"] + join.stats["garbage"], 1)

    def test_bye_then_rejoin(self):
        a, b = FakeSide(), FakeSide(pos=(4.0, 0.0, 4.0))
        host, join = pair(a, b)
        run([host, join], 0.3)
        join.close()
        run([host], 0.1)
        self.assertEqual(a.mail[0], nb.MAIL_BRIDGE)
        self.assertIsNone(host.peer)
        self.assertEqual(b.mail[0], 0)  # leaving clears our own mailbox
        join2 = nb.NetBridge(FakeSide(pos=(8.0, 0.0, 8.0)), udp(), "join", peer=host.sock.getsockname(),
                             log=lambda s: None)
        run([host, join2], 0.4)
        self.assertEqual(a.mail, (nb.MAIL_BRIDGE | nb.MAIL_VISIBLE, (8.0, 0.0, 8.0), 0))

    def test_host_not_up_yet(self):
        a, b = FakeSide(), FakeSide(pos=(2.0, 0.0, 2.0))
        port = udp().getsockname()[1]  # pick a free port, nobody listening yet
        join = nb.NetBridge(b, udp(), "join", peer=("127.0.0.1", port), log=lambda s: None)
        run([join], 0.6)  # HELLOs into the void (Windows may raise ConnectionResetError on recv)
        self.assertFalse(join.connected)
        hs = udp(port)
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        run([host, join], 1.0)
        self.assertTrue(join.connected)
        self.assertEqual(a.mail[1], (2.0, 0.0, 2.0))


@unittest.skipUnless("--chaos" in sys.argv, "pass --chaos")
class ThroughChaosRelay(unittest.TestCase):
    def test_bad_wifi(self):
        a, b = FakeSide(), FakeSide()
        hs = udp()
        host = nb.NetBridge(a, hs, "host", log=lambda s: None)
        info = os.path.join(tempfile.mkdtemp(), "info.json")
        relay = subprocess.Popen([sys.executable, "-I", CHAOS, "relay", "--target", "127.0.0.1:%d" % hs.getsockname()[1],
                                  "--profile", "bad_wifi", "--info-file", info, "--seconds", "20"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(100):
                if os.path.exists(info) and os.path.getsize(info):
                    break
                time.sleep(0.05)
            listen = json.load(open(info))["listen"]
            join = nb.NetBridge(b, udp(), "join", peer=("127.0.0.1", listen), log=lambda s: None)
            t0 = time.monotonic()
            errors, hidden = [], 0

            def move(now):
                t = now - t0
                b.state = ("F000", 0, (200 * math.cos(t), 0.0, 200 * math.sin(t)), 0)

            def sample(now):
                nonlocal hidden
                move(now)
                if a.mail and now - t0 > 2.0:
                    if a.mail[0] & nb.MAIL_VISIBLE:
                        errors.append(math.dist(a.mail[1], b.state[2]))
                    else:
                        hidden += 1

            run([host, join], 8.0, before=sample)
            errors.sort()
            p50, p95 = errors[len(errors) // 2], errors[int(len(errors) * 0.95)]
            print(f"\n  bad_wifi: received {host.stats['received']} sent {join.stats['sent']} stale {host.stats['stale']}"
                  f" | position error p50 {p50:.1f} p95 {p95:.1f} units | hidden samples {hidden}/{hidden + len(errors)}")
            self.assertTrue(join.connected)
            self.assertEqual(hidden, 0)       # loss bursts must not hit the 2 s timeout
            self.assertLess(p95, 80.0)        # 200 units/s, ~70 ms one-way + jitter + loss
        finally:
            relay.terminate()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
