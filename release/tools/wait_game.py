"""Wait until a Dolphin process runs SOUE01 with the co-op hook installed.  python -I wait_game.py PID [SECONDS]"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge"))
import game_side

pid, limit = int(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 120
end = time.time() + limit
last = ""
while time.time() < end:
    try:
        side = game_side.GameSide(pid)
        print(f"game running, hook found at {side.block:#x}")
        sys.exit(0)
    except SystemExit as e:
        if e.code == 0:
            raise
        last = str(e.code)
    except Exception as e:  # process not ready yet
        last = repr(e)
    time.sleep(1)
print("gave up waiting for the game:", last)
sys.exit(1)
