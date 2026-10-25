"""Show the spawn hook's state and the puppet actor next to Link.  python -I puppet_status.py"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
import ss_mem as m

HOOK_STATE = 0x80002364  # state word inside the Gecko C2 hook (observed in Dolphin 2609a)
PROFILE_NAME = 0x08      # fBase_c::mProfileName (u16)

d = m.Dolphin()
link = d.u32(m.LINK_PTR)
pup, cooldown, creates, event, execs, events = struct.unpack(">6I", d.read(HOOK_STATE, 24))
EVENTS = {0: "none", 1: "create ok", 2: "model heap fail", 3: "deleted", 4: "allocator fail"}
print(f"hook block {HOOK_STATE:#x}: puppet={pup:#010x} cooldown={cooldown} creates={creates}")
print(f"puppet log: last event={EVENTS.get(event, event)} events={events} executes={execs}")
print(f"stage {m.spawn_info(d, m.CURRENT_SPAWN)}")
if m.valid_ptr(link):
    print("Link   pos=(%.1f, %.1f, %.1f)" % struct.unpack(">3f", d.read(link + m.ACTOR_POS, 12)),
          f"room={struct.unpack('>b', d.read(link + 0xEC, 1))[0]}")
if m.valid_ptr(pup):
    prof = struct.unpack(">H", d.read(pup + PROFILE_NAME, 2))[0]
    print(f"puppet {pup:#x} profile={prof:#x}",
          "pos=(%.1f, %.1f, %.1f)" % struct.unpack(">3f", d.read(pup + m.ACTOR_POS, 12)),
          f"room={struct.unpack('>b', d.read(pup + 0xEC, 1))[0]}")
else:
    print("no puppet pointer")
