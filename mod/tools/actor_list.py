"""List live actors (fManager_c connect tree) with their placement id (dAcBase_c::mObjID), nearest to Link first.

  python -I actor_list.py PID [--all] [--n 25]
"""
import argparse, struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ss_mem as m

CONNECT_MANAGE = 0x805B84D8   # fManager_c::m_connectManage (cTreeMg_c)
PARENT, CHILD, NEXT, OWNER = 0x0, 0x4, 0xC, 0x10   # cTreeNd_c / fTrNdBa_c::p_owner
CONNECT_NODE = 0x10           # fBase_c::mManager.connect_node
GROUP, PROFILE, STATE, OBJ_ID, POS, ROOM = 0x0E, 0x08, 0x0A, 0xB2, 0xC0, 0xEC
ACTOR, STAGE = 2, 3


def walk(d):
    hdr = d.read(CONNECT_MANAGE, 0x10)
    root = next((w for w in struct.unpack(">4I", hdr) if m.valid_ptr(w)), None)
    node, seen = root, set()
    while node and m.valid_ptr(node) and node not in seen and len(seen) < 5000:
        seen.add(node)
        yield d.u32(node + OWNER)
        child = d.u32(node + CHILD)
        if child:
            node = child
            continue
        while node:
            nxt = d.u32(node + NEXT)
            if nxt:
                node = nxt
                break
            node = d.u32(node + PARENT)


def actors(d):
    for b in walk(d):
        if not m.valid_ptr(b):
            continue
        group, = d.read(b + GROUP, 1)
        if group not in (ACTOR, STAGE):
            continue
        prof, = struct.unpack(">H", d.read(b + PROFILE, 2))
        state, = d.read(b + STATE, 1)
        oid, = struct.unpack(">H", d.read(b + OBJ_ID, 2))
        pos = struct.unpack(">3f", d.read(b + POS, 12))
        room = struct.unpack(">b", d.read(b + ROOM, 1))[0]
        yield dict(addr=b, group=group, profile=prof, state=state, obj_id=oid, pos=pos, room=room)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", type=int)
    ap.add_argument("--all", action="store_true", help="also actors without a placement id")
    ap.add_argument("--n", type=int, default=25)
    a = ap.parse_args()
    d = m.Dolphin(pid=a.pid)
    link = d.u32(m.LINK_PTR)
    lp = struct.unpack(">3f", d.read(link + POS, 12))
    rows = [r for r in actors(d) if a.all or r["obj_id"] != 0xFFFF]
    rows.sort(key=lambda r: sum((p - q) ** 2 for p, q in zip(r["pos"], lp)))
    print(f"{len(rows)} actors")
    for r in rows[:a.n]:
        dist = sum((p - q) ** 2 for p, q in zip(r["pos"], lp)) ** 0.5
        print(f"{r['addr']:#x} grp {r['group']} prof {r['profile']:#05x} id {r['obj_id']:#06x} room {r['room']:2} "
              f"state {r['state']} dist {dist:7.0f}")
