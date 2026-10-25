"""List the folders and entries of a BRRES file (or the g3d/model.brres inside a U8 .arc).

  python -I brres_ls.py FILE [FOLDER_FILTER]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import u8  # noqa: E402


def group(d, off):
    """Entries of an index group at off: [(name, data_offset_absolute)]."""
    _size, n = struct.unpack_from(">II", d, off)
    out = []
    for i in range(1, n + 1):
        e = off + 8 + i * 16
        name_off, data_off = struct.unpack_from(">II", d, e + 8)
        p = off + name_off
        name = d[p:d.index(b"\0", p)].decode("ascii", "replace")
        out.append((name, off + data_off))
    return out


def brres(d):
    if d[:4] != b"bres":
        raise ValueError("not a BRRES")
    root_off = struct.unpack_from(">H", d, 0x0C)[0]
    return {name: [n for n, _ in group(d, off)] for name, off in group(d, root_off + 8)}


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    if data[:4] != b"bres":
        entries, _ = u8.parse(data)
        for path, off, size in entries:
            if path.endswith(".brres"):
                data = data[off:off + size]
                break
    filt = sys.argv[2] if len(sys.argv) > 2 else ""
    for folder, names in brres(data).items():
        if filt.lower() in folder.lower():
            print(f"{folder}: {len(names)}")
            print("   " + ", ".join(names))
