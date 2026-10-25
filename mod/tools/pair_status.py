"""Compare each Dolphin's puppet with the other Dolphin's Link.  python -I pair_status.py PID_A PID_B"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ss_mem as m  # noqa: E402

BLOCK = 0x80002364
EVENTS = {0: "-", 1: "create ok", 2: "model heap fail", 3: "deleted", 4: "allocator fail"}


def side(pid):
    d = m.Dolphin(pid=pid)
    b = d.read(BLOCK, 0x40)
    pup, _, creates, last, execs, events, seq, flags = struct.unpack(">8I", b[:0x20])
    hist_idx = struct.unpack(">I", b[0x38:0x3C])[0]
    n = min(hist_idx, 8)
    history = [EVENTS.get(b[0x30 + ((hist_idx - n + i) & 7)], "?") for i in range(n)]
    link = d.u32(m.LINK_PTR)
    link_pos = struct.unpack(">3f", d.read(link + m.ACTOR_POS, 12)) if m.valid_ptr(link) else None
    pup_pos = None
    if m.valid_ptr(pup) and struct.unpack(">H", d.read(pup + 8, 2))[0] == 0x14E:
        pup_pos = struct.unpack(">3f", d.read(pup + m.ACTOR_POS, 12))
    stage = m.spawn_info(d, m.CURRENT_SPAWN)["stage"]
    return dict(stage=stage, link=link_pos, puppet=pup_pos, creates=creates, execs=execs, seq=seq, flags=flags,
                history=history)


def fmt(p):
    return "-" if p is None else "(%.0f, %.0f, %.0f)" % p


a, b = side(int(sys.argv[1])), side(int(sys.argv[2]))
for name, me, other in (("A", a, b), ("B", b, a)):
    gap = None
    if me["puppet"] and other["link"]:
        gap = sum((x - y) ** 2 for x, y in zip(me["puppet"], other["link"])) ** 0.5
    print(f"{name}: stage {me['stage']} | own Link {fmt(me['link'])} | puppet {fmt(me['puppet'])} | "
          f"other Link {fmt(other['link'])} | gap {'-' if gap is None else f'{gap:.1f}'}")
    print(f"   mail seq {me['seq']} flags {me['flags']} | creates {me['creates']} | puppet frames {me['execs']} | "
          f"history {me['history']}")
