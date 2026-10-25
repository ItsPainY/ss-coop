"""Nintendo U8 archive (.arc) unpack/repack, stdlib only.

  python -I u8.py list ARC
  python -I u8.py unpack ARC OUTDIR
  python -I u8.py pack INDIR ARC [--like ORIGINAL_ARC]

--like copies the original's file order, alignment and padding so that repacking an unmodified
folder reproduces the original byte-for-byte (the check that the packer is correct).
"""

import os
import struct
import sys

MAGIC = 0x55AA382D


def parse(data):
    magic, root_off, hdr_size, data_off = struct.unpack_from(">IIII", data, 0)
    if magic != MAGIC:
        raise ValueError("not a U8 archive")
    total = struct.unpack_from(">I", data, root_off + 8)[0]
    nodes = [struct.unpack_from(">III", data, root_off + i * 12) for i in range(total)]
    str_base = root_off + total * 12

    def name(off):
        end = data.index(b"\0", str_base + off)
        return data[str_base + off:end].decode("shift_jis")

    entries, stack = [], [(total, "")]  # (end index, path prefix)
    for i in range(1, total):
        while i >= stack[-1][0]:
            stack.pop()
        t_name, a, b = nodes[i]
        is_dir, n = t_name >> 24, name(t_name & 0xFFFFFF)
        path = stack[-1][1] + n
        if is_dir:
            entries.append((path + "/", None, None))
            stack.append((b, path + "/"))
        else:
            entries.append((path, a, b))
    return entries, (root_off, hdr_size, data_off)


def cmd_list(arc):
    data = open(arc, "rb").read()
    for path, off, size in parse(data)[0]:
        print(path if off is None else f"{path}  @{off:#x}  {size} bytes")


def cmd_unpack(arc, out):
    data = open(arc, "rb").read()
    for path, off, size in parse(data)[0]:
        dest = os.path.join(out, *path.strip("/").split("/"))
        if off is None:
            os.makedirs(dest, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data[off:off + size])


def cmd_pack(indir, arc, like=None):
    if like is None:
        raise SystemExit("pack currently needs --like ORIGINAL (layout copied from the original)")
    orig = open(like, "rb").read()
    entries, (root_off, hdr_size, data_off) = parse(orig)
    files = [(p, o) for p, o, s in entries if o is not None]
    # Keep the original node table and string table; rewrite file offsets and sizes.
    total = struct.unpack_from(">I", orig, root_off + 8)[0]
    header = bytearray(orig[:data_off])
    out = bytearray()
    first_off = min(o for _, o in files)
    out += orig[data_off:first_off]  # whatever the original had before the first file
    cursor = first_off
    node_index = {}
    for i in range(1, total):
        t_name, a, b = struct.unpack_from(">III", orig, root_off + i * 12)
        if not t_name >> 24:
            node_index[a] = i  # original offset -> node
    ordered = sorted(files, key=lambda x: x[1])
    for n, (path, o) in enumerate(ordered):
        blob = open(os.path.join(indir, *path.split("/")), "rb").read()
        struct.pack_into(">II", header, root_off + node_index[o] * 12 + 4, cursor, len(blob))
        out += blob
        cursor += len(blob)
        if n < len(ordered) - 1:  # files start 32-byte aligned; the last one is not padded
            pad = (-cursor) % 32
            out += b"\0" * pad
            cursor += pad
    result = bytes(header) + bytes(out)
    with open(arc, "wb") as f:
        f.write(result)
    print(f"wrote {arc}: {len(result)} bytes ({len(files)} files)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["list"]:
        cmd_list(a[1])
    elif a[:1] == ["unpack"]:
        cmd_unpack(a[1], a[2])
    elif a[:1] == ["pack"]:
        cmd_pack(a[1], a[2], a[a.index("--like") + 1] if "--like" in a else None)
    else:
        raise SystemExit(__doc__)
