"""Set [Core] EnableCheats = True in a Dolphin user dir's Config/Dolphin.ini (keeps other settings)."""
import configparser, os, sys

ini = os.path.join(sys.argv[1], "Config", "Dolphin.ini")
os.makedirs(os.path.dirname(ini), exist_ok=True)
c = configparser.ConfigParser(strict=False, interpolation=None)
c.optionxform = str
if os.path.exists(ini):
    c.read(ini, encoding="utf-8")
if not c.has_section("Core"):
    c.add_section("Core")
c.set("Core", "EnableCheats", "True")
with open(ini, "w", encoding="utf-8") as f:
    c.write(f, space_around_delimiters=True)
print("cheats enabled in", ini)
