"""Local two-instance bridge: each Dolphin's Link drives the puppet in the other Dolphin.

  python -I local_bridge.py PID_A PID_B [--hz 60] [--seconds N]

Both Dolphins must run the modded game with the spawn hook (mod/scripts/start_test.ps1). For two PCs use
net_bridge.py instead.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from game_side import GameSide, MAIL_BRIDGE, MAIL_VISIBLE  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pid_a", type=int)
    ap.add_argument("pid_b", type=int)
    ap.add_argument("--hz", type=float, default=60.0)
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    args = ap.parse_args()

    a, b = GameSide(args.pid_a), GameSide(args.pid_b)
    print(f"A pid {args.pid_a} block {a.block:#x} | B pid {args.pid_b} block {b.block:#x}", flush=True)
    period, end, last_print = 1.0 / args.hz, (time.time() + args.seconds) if args.seconds else None, 0.0
    try:
        while end is None or time.time() < end:
            t0 = time.time()
            sa, sb = a.link_state(), b.link_state()
            for dst, s_src, s_dst in ((b, sa, sb), (a, sb, sa)):
                if s_src is not None and s_dst is not None and s_src[0] == s_dst[0]:
                    dst.write_mail(MAIL_BRIDGE | MAIL_VISIBLE, s_src[2], s_src[3], tunic=s_src[4], anim=s_src[5],
                                   equip=s_src[6], held=s_src[7])
                else:
                    dst.write_mail(MAIL_BRIDGE)
            if t0 - last_print > 2.0:
                last_print = t0
                fmt = lambda s: "no Link" if s is None else f"{s[0]} r{s[1]} ({s[2][0]:.0f}, {s[2][1]:.0f}, {s[2][2]:.0f})"
                print(f"A: {fmt(sa)} | B: {fmt(sb)}", flush=True)
            time.sleep(max(0.0, period - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        for side in (a, b):
            try:
                side.write_mail(0)  # bridge gone: puppets fall back to standing where they spawned
            except OSError:
                pass
        print("bridge stopped, mailboxes cleared", flush=True)


if __name__ == "__main__":
    main()
