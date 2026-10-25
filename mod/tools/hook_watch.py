"""Watch the spawn hook's block and the puppet module's link state.  python -I hook_watch.py PID [SECONDS]"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ss_mem as m  # noqa: E402

BLOCK = 0x80002364
PROFILE_LIST = 0x80575BC0  # fProfile::sProfileList
PUPPET_PROFILE = 0x14E

d = m.Dolphin(pid=int(sys.argv[1]))
end = time.time() + (float(sys.argv[2]) if len(sys.argv) > 2 else 3)
while time.time() < end:
    b = d.read(BLOCK, 0xB0)
    pup, cool, creates = struct.unpack(">3I", b[:12])
    flags = struct.unpack(">I", b[0x1C:0x20])[0]
    hook_off = struct.unpack(">I", b[0x3C:0x40])[0]
    entry = d.u32(d.u32(PROFILE_LIST) + 4 * PUPPET_PROFILE)
    print(f"puppet={pup:#x} cooldown={cool} creates={creates} flags={flags} hookOff={hook_off:#x} | "
          f"profile entry={entry:#x} | LINK={d.u32(m.LINK_PTR):#x}", flush=True)
    time.sleep(0.5)
