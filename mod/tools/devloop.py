"""Dev loop for the puppet REL: no replaying, no owner needed after one savestate.

  python -I devloop.py arm PID
      Switch the spawn hook off in a running test Dolphin (before loading the save), so the puppet REL is
      never linked. Load the save, then save state (Shift+F1 -> slot 1).
  python -I devloop.py run STATE USERDIR [--rel REL] [--seconds N]
      Boot Dolphin from STATE, write REL (default game/puppet-latest.rel) into the RAM copy of rels.arc,
      switch the hook on, print the puppet status. Prints the Dolphin pid.
  python -I devloop.py walk PID [--seconds N] [--radius R] [--speed S]
      Act as a fake remote player circling the local Link (drives walk/run animations).
  python -I devloop.py shot PID OUT_PREFIX
      Screenshot every visible window of that Dolphin to OUT_PREFIX-<n>.png.

Why this works: rels.arc is loaded into RAM once at boot and modules are linked from that RAM copy on demand.
In a state saved with the hook off, our module is not linked yet, so replacing its bytes in RAM takes effect.
build_mod.ps1 gives the REL a fixed 0x8000-byte slot so the archive layout matches across builds.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import math
import os
import struct
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "mod", "bridge"))
import ss_mem as m  # noqa: E402
import u8  # noqa: E402
from game_side import GameSide  # noqa: E402

HOOK_OFF = 0x3C
HOOK_OFF_MAGIC = 0x0FF00FF0
REL_NAME = "rels/d_a_morita_testNP.rel"
SLOT = 0x8000
MAIL_SEQ, MAIL_FLAGS, MAIL_POS, MAIL_TUNIC = 0x18, 0x1C, 0x20, 0x2E


def arm(pid):
    side = GameSide(pid)
    side.d = m.Dolphin(writable=True, pid=pid)
    side.d.write(side.block + HOOK_OFF, struct.pack(">I", HOOK_OFF_MAGIC))
    print(f"hook off in pid {pid} (block {side.block:#x})")


def find_rels_archive(d):
    """Host address of rels.arc in guest RAM and the file offset of our REL inside it."""
    regions = d._mapped_regions()
    seen = set()
    for base, size in regions:
        if size < 0x1000000 or (base, size) in seen:
            continue
        seen.add((base, size))
        data = d._raw(base, size)
        if not data:
            continue
        pos = 0
        while True:
            pos = data.find(b"\x55\xAA\x38\x2D", pos)
            if pos < 0:
                break
            try:
                entries, _ = u8.parse(data[pos:pos + 0x100000])  # header + node table are near the start
            except Exception:
                pos += 4
                continue
            for path, off, sz in entries:
                if path == REL_NAME:
                    return base + pos, off, sz
            pos += 4
    return None


def run(state, user_dir, rel_path, seconds):
    dol = os.path.join(ROOT, "game", "extracted", "DATA", "sys", "main.dol")
    exe = os.path.join(ROOT, "tools", "Dolphin-x64", "Dolphin.exe")
    p = subprocess.Popen([exe, "-u", user_dir, "-e", dol, "-s", state])
    print(f"Dolphin pid {p.pid}, loading state", flush=True)
    side = None
    for _ in range(120):
        try:
            side = GameSide(p.pid)
            break
        except (SystemExit, Exception):
            time.sleep(0.5)
    if side is None:
        raise SystemExit("game did not come up")
    # Dolphin boots the game first and loads the state afterwards; writing before the state has landed gets
    # overwritten by it (seen 2026-10-25: old code kept running). Wait for the state's marks: hook off + Link.
    d = m.Dolphin(writable=True, pid=p.pid)
    for _ in range(120):
        hook_off = struct.unpack(">I", d.read(side.block + HOOK_OFF, 4))[0]
        if hook_off == HOOK_OFF_MAGIC and m.valid_ptr(d.u32(m.LINK_PTR)):
            break
        time.sleep(0.5)
    else:
        raise SystemExit("state did not load (hook not off / no Link)")
    time.sleep(1.0)
    found = find_rels_archive(d)
    if not found:
        raise SystemExit("rels.arc not found in RAM")
    host, off, size = found
    rel = open(rel_path, "rb").read()
    if len(rel) > SLOT or size != SLOT:
        raise SystemExit(f"slot mismatch: REL {len(rel)} bytes, slot in RAM {size} (expected {SLOT})")
    buf = rel + b"\0" * (SLOT - len(rel))
    # The game also keeps a loaded (not yet linked) copy of each module outside the archive (seen 2026-10-25:
    # swapping only the archive copy left the old code running). Find every copy of the boot-time REL by its
    # first 0x40 bytes and replace them all.
    old_head = d._raw(host + off, 0x40)
    copies = []
    seen = set()
    for base, rsize in d._mapped_regions():
        if rsize < 0x1000000 or (base, rsize) in seen:
            continue
        seen.add((base, rsize))
        data = d._raw(base, rsize) or b""
        hit = data.find(old_head)
        while hit >= 0:
            copies.append(base + hit)
            print(f"  copy in region {base:#x} (size {rsize:#x}) at offset {hit:#x}", flush=True)
            hit = data.find(old_head, hit + 1)
    print(f"boot-time REL found at {len(copies)} host address(es)", flush=True)
    got = ctypes.c_size_t()
    for addr in copies:
        if not m.k32.WriteProcessMemory(d.h, ctypes.c_void_p(addr), buf, len(buf), ctypes.byref(got)):
            raise SystemExit(f"write failed at {addr:#x}")
    print(f"REL ({len(rel)} bytes) written to all copies", flush=True)
    d.write(side.block + HOOK_OFF, struct.pack(">I", 0))
    print("hook on", flush=True)
    time.sleep(seconds)
    status(p.pid)
    return p.pid  # noqa: the Popen object, not the loop variable


def status(pid):
    d = m.Dolphin(pid=pid)
    side = GameSide(pid)
    b = d.read(side.block, 0x40)
    pup, cooldown, creates, last, execs, events = struct.unpack(">6I", b[:0x18])
    hidx = struct.unpack(">I", b[0x38:0x3C])[0]
    hist = [b[0x30 + ((hidx - min(hidx, 8) + i) & 7)] for i in range(min(hidx, 8))]
    names = {1: "create ok", 2: "heap fail", 3: "deleted", 4: "alloc fail", 5: "ANIM FAIL", 6: "tunic change",
             7: "anim buffer fail", 8: "anim load fail", 9: "anim create fail", 10: "copy alloc fail"}
    print(f"puppet {pup:#x} build {b[0x2F]} creates {creates} frames {execs} "
          f"events {[names.get(h, h) for h in hist]}", flush=True)


def walk(pid, seconds, radius, speed):
    side = GameSide(pid)
    d = m.Dolphin(writable=True, pid=pid)
    tunic = d.read(d.u32(m.LINK_PTR) + 0x120B, 1)[0]
    t0, seq = time.time(), 0
    while time.time() - t0 < seconds:
        st = side.link_state()
        if st:
            x, y, z = st[2]
            t = time.time() - t0
            ang = speed * t / radius + 0.6  # radians; speed in units per second (0 = stand still)
            px, pz = x + radius * math.cos(ang), z + radius * math.sin(ang)
            face = int(math.degrees(math.atan2(-math.sin(ang), math.cos(ang))) / 360 * 65536) & 0xFFFF  # tangent
            d.write(side.block + MAIL_POS, struct.pack(">3fH", px, y, pz, face))
            d.write(side.block + MAIL_TUNIC, bytes([tunic]))
            d.write(side.block + MAIL_FLAGS, struct.pack(">I", 3))
            seq += 1
            d.write(side.block + MAIL_SEQ, struct.pack(">I", seq))
        time.sleep(1 / 60)
    d.write(side.block + MAIL_FLAGS, struct.pack(">I", 0))


# --- screenshots (PrintWindow, PNG via zlib) ---
user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def windows_of(pid):
    out = []

    def cb(h, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h):
            out.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def png(path, w, h, bgra):
    rows = b"".join(b"\0" + bytes(px for i in range(w) for px in bgra[(y * w + i) * 4:(y * w + i) * 4 + 3][::-1])
                    for y in range(h))
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def shot(pid, prefix, crop=None):
    """crop = (x0, y0, x1, y1) in window pixels, saved 2x enlarged, for a closer look."""
    paths = []
    for n, h in enumerate(windows_of(pid)):
        r = wt.RECT()
        user32.GetClientRect(h, ctypes.byref(r))
        w, hh = r.right - r.left, r.bottom - r.top
        if w < 64 or hh < 64:
            continue
        hdc = user32.GetDC(h)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, hh)
        gdi32.SelectObject(mdc, bmp)
        user32.PrintWindow(h, mdc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -hh, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * hh * 4)
        gdi32.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)
        gdi32.DeleteObject(bmp), gdi32.DeleteDC(mdc), user32.ReleaseDC(h, hdc)
        path = f"{prefix}-{n}.png"
        raw = buf.raw
        if crop and w >= crop[2] and hh >= crop[3]:
            x0, y0, x1, y1 = crop
            rows = [raw[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1)]
            big = b"".join(b"".join(r[i:i + 4] * 2 for i in range(0, len(r), 4)) * 2 for r in rows)
            w, hh, raw = (x1 - x0) * 2, (y1 - y0) * 2, big
        png(path, w, hh, raw)
        paths.append(path)
        print(path, w, hh, flush=True)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("arm"); a.add_argument("pid", type=int)
    r = sub.add_parser("run"); r.add_argument("state"); r.add_argument("user_dir")
    r.add_argument("--rel", default=os.path.join(ROOT, "game", "puppet-latest.rel"))
    r.add_argument("--seconds", type=float, default=4.0)
    w = sub.add_parser("walk"); w.add_argument("pid", type=int); w.add_argument("--seconds", type=float, default=10)
    w.add_argument("--radius", type=float, default=150); w.add_argument("--speed", type=float, default=150)
    s = sub.add_parser("shot"); s.add_argument("pid", type=int); s.add_argument("prefix")
    s.add_argument("--crop", type=int, nargs=4)
    st = sub.add_parser("status"); st.add_argument("pid", type=int)
    args = ap.parse_args()
    if args.cmd == "arm":
        arm(args.pid)
    elif args.cmd == "run":
        run(args.state, args.user_dir, args.rel, args.seconds)
    elif args.cmd == "walk":
        walk(args.pid, args.seconds, args.radius, args.speed)
    elif args.cmd == "shot":
        shot(args.pid, args.prefix, args.crop)
    else:
        status(args.pid)
