"""Assemble spawn_puppet.s into a Gecko C2 code and write it into a Dolphin user dir.

  python -I build_gecko.py PUPPET_PROFILE_HEX DOLPHIN_USER_DIR

Uses the decomp's binutils (tools/decomp-tools/binutils). Enables cheats in that user dir's Dolphin.ini.
"""

import configparser
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "tools", "decomp-tools", "binutils")
HOOK_ADDR = 0x801BA700  # dScGame_c::execute
CODE_NAME = "Co-op puppet spawn"


def assemble(profile):
    with tempfile.TemporaryDirectory() as t:
        obj, raw = os.path.join(t, "h.o"), os.path.join(t, "h.bin")
        subprocess.run([os.path.join(BIN, "powerpc-eabi-as.exe"), "-mgekko", "-mregnames", "--fatal-warnings",
                        "--defsym", f"PUPPET_PROFILE={profile}", "-o", obj,
                        os.path.join(HERE, "spawn_puppet.s")], check=True)
        subprocess.run([os.path.join(BIN, "powerpc-eabi-objcopy.exe"), "-O", "binary", "-j", ".text", obj, raw],
                       check=True)
        return open(raw, "rb").read()


def gecko_c2(addr, code):
    words = list(struct.unpack(f">{len(code) // 4}I", code))
    if len(words) % 2 == 0:
        words.append(0x60000000)  # nop, so the branch-back word lands in the second column
    words.append(0x00000000)      # replaced by the handler with a branch back to addr + 4
    lines = [f"C2{addr & 0x01FFFFFF:06X} {len(words) // 2:08X}"]
    lines += [f"{words[i]:08X} {words[i + 1]:08X}" for i in range(0, len(words), 2)]
    return lines


def write_ini(user_dir, lines):
    gs = os.path.join(user_dir, "GameSettings")
    os.makedirs(gs, exist_ok=True)
    with open(os.path.join(gs, "SOUE01.ini"), "w", newline="\n") as f:
        f.write("[Gecko]\n$" + CODE_NAME + "\n" + "\n".join(lines) + "\n")
        f.write("[Gecko_Enabled]\n$" + CODE_NAME + "\n")
    cfg_dir = os.path.join(user_dir, "Config")
    os.makedirs(cfg_dir, exist_ok=True)
    ini = os.path.join(cfg_dir, "Dolphin.ini")
    c = configparser.ConfigParser(strict=False, interpolation=None)
    c.optionxform = str
    if os.path.exists(ini):
        c.read(ini, encoding="utf-8")
    if not c.has_section("Core"):
        c.add_section("Core")
    c.set("Core", "EnableCheats", "True")
    with open(ini, "w", encoding="utf-8") as f:
        c.write(f, space_around_delimiters=True)


if __name__ == "__main__":
    profile, user_dir = int(sys.argv[1], 16), sys.argv[2]
    lines = gecko_c2(HOOK_ADDR, assemble(profile))
    write_ini(user_dir, lines)
    print("\n".join(lines))
