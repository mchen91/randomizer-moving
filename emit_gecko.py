#!/usr/bin/env python3
"""Format a flat PPC blob as a Gecko C2 (Insert ASM) code for Dolphin.

C2 layout:
  C2AAAAAA NNNNNNNN     ; AAAAAA = hook & 0x01FFFFFF, NNNNNNNN = number of 8-byte lines
  IIIIIIII IIIIIIII     ; the blob instructions, two words per line
  ...                   ; padded to an 8-byte boundary with nops (0x60000000)

The codehandler writes a branch at the hook to this block and appends a branch
back to hook+4 after the block, so the blob must NOT include that return branch.
"""
import struct
import sys


def main():
    if len(sys.argv) < 4:
        print("usage: emit_gecko.py BLOB BIN HOOK_HEX TITLE", file=sys.stderr)
        sys.exit(1)
    blob_path, hook_hex, title = sys.argv[1], sys.argv[2], sys.argv[3]
    hook = int(hook_hex, 16)
    data = open(blob_path, "rb").read()
    if len(data) % 4:
        data += b"\x00" * (4 - len(data) % 4)
    words = list(struct.unpack(">%dI" % (len(data) // 4), data))
    if len(words) % 2:
        words.append(0x60000000)
    nlines = len(words) // 2

    print(f"${title}")
    print("C2%06X %08X" % (hook & 0x01FFFFFF, nlines))
    for i in range(0, len(words), 2):
        print("%08X %08X" % (words[i], words[i + 1]))


if __name__ == "__main__":
    main()
