"""sscoop.exe: every Python helper of the Skyward Sword co-op package in one program (built with PyInstaller),
so players do not need Python.

  sscoop.exe bridge ...   = bridge/net_bridge.py ...
  sscoop.exe u8 ...       = tools/u8.py ...
  sscoop.exe cheats DIR   = tools/enable_cheats.py DIR
  sscoop.exe wait PID [S] = tools/wait_game.py PID [S]
"""

import runpy
import sys

# The modules are bundled via --hidden-import in build_exe.ps1 and run as __main__ below. Not imported here:
# enable_cheats.py and wait_game.py do their work at import time.
COMMANDS = {"bridge": "net_bridge", "u8": "u8", "cheats": "enable_cheats", "wait": "wait_game"}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    module = COMMANDS[sys.argv[1]]
    sys.argv = [module + ".py"] + sys.argv[2:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
