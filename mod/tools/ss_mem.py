"""Read Skyward Sword (SOUE01, Rev 0) state from a running Dolphin, stdlib only.

Finds Dolphin's emulated RAM the same way Dolphin Memory Engine does (a MEM_MAPPED view of
0x2000000 bytes for MEM1, MEM2 at MEM1 + 0x10000000), then reads addresses taken from the
zeldaret/ss symbol map (config/SOUE01/symbols.txt). Read-only: never writes game memory.

Usage:
  python -I ss_mem.py status          one snapshot
  python -I ss_mem.py watch [secs]    print position/stage every 0.5 s
  python -I ss_mem.py flags OUT.json  dump all live flag words for diffing
  python -I ss_mem.py diff A.json B.json
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys
import time

GAME_ID = b"SOUE01"

# Addresses from zeldaret/ss @ 96f2dd54, config/SOUE01/symbols.txt
LINK_PTR = 0x8057578C            # dAcPy_c::LINK (dAcPy_c *)
FILE_MGR_PTR = 0x80574FFC        # FileManager::sInstance
CURRENT_SPAWN = 0x805B3860       # dScGame_c::currentSpawnInfo (SpawnInfo, 0x2A bytes)
NEXT_SPAWN = 0x805B388C          # dScGame_c::nextSpawnInfo

# Live flag arrays (u16 words). Saved to the SaveFile on commit.
FLAGS = {
    "story":   (0x805A9AD8, 0x80),   # StoryflagManager::sFlags
    "item":    (0x805A9BD8, 0x40),   # ItemflagManager::sFlags
    "dungeon": (0x805A9C58, 0x08),   # DungeonflagManager::sDungeonFlags (current dungeon)
    "scene":   (0x805A78D0, 0x08),   # SceneflagManager::sSceneFlags (current scene)
    "zone":    (0x805A78E0, 0xFC),   # SceneflagManager::sZoneFlags
    "temp":    (0x805753E8, 0x04),   # SceneflagManager::sTempFlags
    "skip":    (0x805A9C68, 0x10),   # SkipflagManager::sSkipFlags
    "tbox":    (0x805753F4, 0x02),   # TBoxflagManager::sTBoxFlags
}

# dAcBase_c field offsets (include/d/a/d_a_base.h)
ACTOR_ROT = 0xB8    # mAng3_c: 3 x s16
ACTOR_POS = 0xC0    # mVec3_c: 3 x f32

# FileManager / SaveFile offsets (include/toBeSorted/file_manager.h, save_file.h)
FM_FILE_A = 0x0008
SF_HEALTH_CAP = 0x5302
SF_HEALTH = 0x5306

MEM1_BASE, MEM1_SIZE = 0x80000000, 0x01800000
MEM2_BASE, MEM2_SIZE = 0x90000000, 0x04000000

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_MAPPED = 0x40000
MEM_COMMIT = 0x1000


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("_a1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_a2", wt.DWORD),
    ]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]


def find_dolphin_pid():
    # Several Dolphins (two-player tests): pick one with SS_DOLPHIN_PID.
    if os.environ.get("SS_DOLPHIN_PID"):
        return int(os.environ["SS_DOLPHIN_PID"])
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Dolphin.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "dolphin.exe":
            return int(parts[1])
    return None


class Dolphin:
    def __init__(self, writable=False, pid=None):
        pid = pid or find_dolphin_pid()
        self.pid = pid
        if pid is None:
            raise SystemExit("Dolphin.exe is not running")
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.h = k32.OpenProcess(access, False, pid)
        if not self.h:
            raise SystemExit(f"OpenProcess failed: {ctypes.get_last_error()}")
        self.mem1 = self.mem2 = None
        regions = self._mapped_regions()
        for base, size in regions:
            if size == 0x2000000 and self._raw(base, 6) == GAME_ID:
                self.mem1 = base
                break
        if self.mem1 is None:
            raise SystemExit("Game RAM not found: is SOUE01 running (not just the Dolphin menu)?")
        for base, size in regions:
            if base == self.mem1 + 0x10000000 and size >= MEM2_SIZE:
                self.mem2 = base
        if self.mem2 is None:
            cands = [b for b, s in regions if s == MEM2_SIZE]
            self.mem2 = cands[0] if cands else None

    def _mapped_regions(self):
        out, addr, mbi = [], 0, MBI()
        while k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            if mbi.Type == MEM_MAPPED and mbi.State == MEM_COMMIT:
                out.append((mbi.BaseAddress, mbi.RegionSize))
            addr = mbi.BaseAddress + mbi.RegionSize
            if addr >= 0x7FFFFFFFFFFF:
                break
        return out

    def _raw(self, host_addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(host_addr), buf, n, ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def read(self, gc_addr, n):
        if MEM1_BASE <= gc_addr and gc_addr + n <= MEM1_BASE + MEM1_SIZE:
            return self._raw(self.mem1 + (gc_addr - MEM1_BASE), n)
        if self.mem2 is not None and MEM2_BASE <= gc_addr and gc_addr + n <= MEM2_BASE + MEM2_SIZE:
            return self._raw(self.mem2 + (gc_addr - MEM2_BASE), n)
        return None

    def write(self, gc_addr, data):
        """Write guest RAM (MEM1 only for now). Needs Dolphin(writable=True)."""
        if not (MEM1_BASE <= gc_addr and gc_addr + len(data) <= MEM1_BASE + MEM1_SIZE):
            raise ValueError(f"write outside MEM1: {gc_addr:#x}")
        got = ctypes.c_size_t()
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(self.mem1 + (gc_addr - MEM1_BASE)), data, len(data),
                                    ctypes.byref(got))
        if not ok or got.value != len(data):
            raise OSError(f"WriteProcessMemory failed: {ctypes.get_last_error()}")

    def u32(self, a):
        b = self.read(a, 4)
        return struct.unpack(">I", b)[0] if b else None

    def u16s(self, a, count):
        b = self.read(a, count * 2)
        return list(struct.unpack(f">{count}H", b)) if b else None


def valid_ptr(p):
    return p is not None and (MEM1_BASE <= p < MEM1_BASE + MEM1_SIZE or MEM2_BASE <= p < MEM2_BASE + MEM2_SIZE)


def spawn_info(d, addr):
    b = d.read(addr, 0x2A)
    if not b:
        return None
    return {
        "stage": b[0:32].split(b"\0")[0].decode("ascii", "replace"),
        "room": b[0x22], "layer": b[0x23], "entrance": b[0x24], "night": b[0x25], "trial": b[0x26],
    }


def snapshot(d):
    s = {"game_id": d.read(MEM1_BASE, 6).decode("ascii", "replace"),
         "mem1_host": hex(d.mem1), "mem2_host": hex(d.mem2) if d.mem2 else None}
    link = d.u32(LINK_PTR)
    s["link_ptr"] = hex(link) if link is not None else None
    if valid_ptr(link):
        s["link_pos"] = struct.unpack(">3f", d.read(link + ACTOR_POS, 12))
        s["link_rot"] = struct.unpack(">3h", d.read(link + ACTOR_ROT, 6))
    s["current_spawn"] = spawn_info(d, CURRENT_SPAWN)
    s["next_spawn"] = spawn_info(d, NEXT_SPAWN)
    fm = d.u32(FILE_MGR_PTR)
    s["file_mgr_ptr"] = hex(fm) if fm is not None else None
    if valid_ptr(fm):
        hp = d.read(fm + FM_FILE_A + SF_HEALTH_CAP, 6)
        if hp:
            cap, _, cur = struct.unpack(">3H", hp)
            s["fileA_health"] = f"{cur}/{cap} quarter-hearts"
    return s


def dump_flags(d):
    return {name: d.u16s(addr, n) for name, (addr, n) in FLAGS.items()}


def diff_flags(a, b):
    for name in FLAGS:
        for i, (x, y) in enumerate(zip(a.get(name) or [], b.get(name) or [])):
            if x != y:
                changed = x ^ y
                bits = [i * 16 + bit for bit in range(16) if changed >> bit & 1]
                print(f"{name}[{i:3}] {x:04x} -> {y:04x}  bits(word*16+bit): {bits}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "diff":
        with open(sys.argv[2]) as fa, open(sys.argv[3]) as fb:
            diff_flags(json.load(fa), json.load(fb))
        return
    d = Dolphin()
    if cmd == "status":
        for k, v in snapshot(d).items():
            print(f"{k:14} {v}")
        nz = {k: sum(1 for w in (v or []) if w) for k, v in dump_flags(d).items()}
        print(f"{'nonzero words':14} {nz}")
    elif cmd == "watch":
        end = time.time() + float(sys.argv[2]) if len(sys.argv) > 2 else None
        while end is None or time.time() < end:
            s = snapshot(d)
            pos = s.get("link_pos")
            p = "(%.1f, %.1f, %.1f)" % pos if pos else "no Link"
            print(f"{s['current_spawn']}  pos={p}  rotY={s.get('link_rot', (0, 0, 0))[1]}", flush=True)
            time.sleep(0.5)
    elif cmd == "flags":
        with open(sys.argv[2], "w") as f:
            json.dump(dump_flags(d), f)
        print("wrote", sys.argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
