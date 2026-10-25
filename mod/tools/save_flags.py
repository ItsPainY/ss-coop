"""Read story/item/scene flags from a wiiking2.sav (NAND save, SOUE01). Layout from the decomp:
SavedSaveFiles (file_manager.h): regionCode[4], ..., SaveFile saveFiles[3] at 0x20, each 0x53C0 bytes
(save_file.h: story_flags +0x8E4, item_flags +0x9E4, scene_flags +0x1A64). Total 0xFBE0 bytes.

  python -I save_flags.py OLD.sav NEW.sav [--slot N] [--live-pid PID]
"""
import os
import struct
import sys

SLOT_BASE, SLOT_SIZE, TOTAL = 0x20, 0x53C0, 0xFBE0
ARRAYS = {"story": (0x8E4, 128), "item": (0x9E4, 64), "scene": (0x1A64, 208)}


def read(path, slot):
    data = open(path, "rb").read()
    if len(data) != TOTAL:
        raise SystemExit(f"{path}: {len(data)} bytes, expected {TOTAL:#x}")
    base = SLOT_BASE + slot * SLOT_SIZE
    return data[:4], {n: list(struct.unpack_from(f">{c}H", data, base + off)) for n, (off, c) in ARRAYS.items()}


args = [a for a in sys.argv[1:] if not a.startswith("--")]
slot = int(sys.argv[sys.argv.index("--slot") + 1]) if "--slot" in sys.argv else 0
region_old, old = read(args[0], slot)
region_new, new = read(args[1], slot)
print(f"region {region_old} / {region_new}, slot {slot}")
for name in old:
    print(f"{name}: old {sum(1 for w in old[name] if w)} words set, new {sum(1 for w in new[name] if w)}")
    for i, (x, y) in enumerate(zip(old[name], new[name])):
        if x != y:
            print(f"  {name}[{i}] {x:04x} -> {y:04x}")
if "--live-pid" in sys.argv:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge"))
    from game_side import GameSide
    live = GameSide(int(sys.argv[sys.argv.index("--live-pid") + 1])).flag_view()
    for name in ("story", "item"):
        diff = [i for i in range(len(new[name])) if new[name][i] != live[name][i]]
        print(f"new save vs live game {name}: {len(diff)} words differ {diff[:10]}")
