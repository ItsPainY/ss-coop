"""Free space in the game's work heaps (RVL expanded heaps), read from outside.  python -I heap_free.py PID

dHeap::{work1,work2,workEx}Heap (symbols.txt) hold an EGG::Heap*; EGG::Heap::mHeapHandle (+0x10) is the RVL
MEMiHeapHead. Layout (RVL SDK): +0x18 heapStart, +0x1C heapEnd, then MEMiExpHeapHead at +0x3C whose first word is
the free list head. Free blocks: u16 'FR', u16 settings, u32 size (+0x4), prev (+0x8), next (+0xC), 0x10 header.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ss_mem as m  # noqa: E402

HEAPS = {"work1Heap": 0x805751A8, "work2Heap": 0x805751AC, "workExHeap": 0x805751B0}


def walk(d, egg_heap):
    head = d.u32(egg_heap + 0x10)
    start, end = d.u32(head + 0x18), d.u32(head + 0x1C)
    block = d.u32(head + 0x3C)
    free, largest, count = 0, 0, 0
    while block and count < 10000:
        sig, size, nxt = struct.unpack(">H2xI4xI", d.read(block, 0x10))
        if sig != 0x4652:  # 'FR'
            return start, end, None, None, count
        free += size
        largest = max(largest, size)
        count += 1
        block = nxt
    return start, end, free, largest, count


if __name__ == "__main__":
    d = m.Dolphin(pid=int(sys.argv[1]))
    for name, sym in HEAPS.items():
        eh = d.u32(sym)
        start, end, free, largest, count = walk(d, eh)
        if free is None:
            print(f"{name:10} {start:#x}-{end:#x}: free list not readable")
        else:
            print(f"{name:10} size {(end - start) // 1024:6} KB | free {free // 1024:6} KB in {count} blocks, "
                  f"largest {largest // 1024} KB")
