"""Nintendo LZ77 (types 0x10 and 0x11) decompression, stdlib only.

  python -I nlz.py IN.LZ OUT
"""

import sys


def decompress(data):
    kind = data[0]
    size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if size == 0:
        size = int.from_bytes(data[4:8], "little")
        pos = 8
    out = bytearray()
    if kind not in (0x10, 0x11):
        raise ValueError(f"unsupported LZ type {kind:#x}")
    while len(out) < size:
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if not flags >> bit & 1:
                out.append(data[pos])
                pos += 1
                continue
            if kind == 0x10:
                b0, b1 = data[pos], data[pos + 1]
                pos += 2
                length = (b0 >> 4) + 3
                disp = ((b0 & 0xF) << 8 | b1) + 1
            else:
                ind = data[pos] >> 4
                if ind == 0:
                    b0, b1, b2 = data[pos:pos + 3]
                    pos += 3
                    length = ((b0 & 0xF) << 4 | b1 >> 4) + 0x11
                    disp = ((b1 & 0xF) << 8 | b2) + 1
                elif ind == 1:
                    b0, b1, b2, b3 = data[pos:pos + 4]
                    pos += 4
                    length = ((b0 & 0xF) << 12 | b1 << 4 | b2 >> 4) + 0x111
                    disp = ((b2 & 0xF) << 8 | b3) + 1
                else:
                    b0, b1 = data[pos], data[pos + 1]
                    pos += 2
                    length = ind + 1
                    disp = ((b0 & 0xF) << 8 | b1) + 1
            start = len(out) - disp
            for i in range(length):
                out.append(out[start + i])
    return bytes(out[:size])


if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        d = decompress(f.read())
    with open(sys.argv[2], "wb") as f:
        f.write(d)
    print(f"{len(d)} bytes")
