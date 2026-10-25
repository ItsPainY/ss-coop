"""Compare the synced flag arrays (live + save copy of file A) of two Dolphins.

  python -I flag_compare.py PID_A PID_B [--dump OUT.json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge"))
from game_side import GameSide, FLAG_ARRAYS  # noqa: E402
import ss_mem as m  # noqa: E402


def save_copy(side):
    fa = side._file_a()
    return {name: side.d.u16s(fa + off, n) for name, (off, n, _live) in FLAG_ARRAYS.items()}


pids = [int(p) for p in sys.argv[1:3]]
sides = [GameSide(p) for p in pids]
views = [s.flag_view() for s in sides]
saves = [save_copy(s) for s in sides]
for label, v in zip("AB", views):
    print(f"{label}: " + ("no Link" if v is None else ", ".join(f"{k} {sum(1 for w in ws if w)} words set" for k, ws in v.items()))
          + f" | scene index {getattr(sides['AB'.index(label)], '_scene_idx', '?')}")
if None not in views:
    diffs = [(k, i, views[0][k][i], views[1][k][i]) for k in views[0] for i in range(len(views[0][k]))
             if views[0][k][i] != views[1][k][i]]
    print(f"live view differences A vs B: {len(diffs)}")
    for k, i, x, y in diffs[:20]:
        print(f"  {k}[{i}] A={x:04x} B={y:04x}")
    for label, v, s in zip("AB", views, saves):
        lag = [(k, i) for k in ("story", "item") for i in range(len(v[k])) if v[k][i] != s[k][i]]
        print(f"{label}: live vs save-copy differences (story/item): {len(lag)}")
if "--dump" in sys.argv:
    with open(sys.argv[sys.argv.index("--dump") + 1], "w") as f:
        json.dump({"views": views, "saves": saves}, f)
