"""Require signing to preserve the unsigned PE, apart from Authenticode fields."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct


def pe_fields(data: bytes) -> tuple[int, int]:
    if data[:2] != b'MZ' or len(data) < 64:
        raise ValueError('Invalid PE DOS header')
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    optional = pe + 24
    if optional + 2 > len(data):
        raise ValueError('Truncated PE optional header')
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError('Unsupported PE optional header')
    checksum = optional + 64
    security = optional + (96 if magic == 0x10b else 112) + 4 * 8
    if security + 8 > len(data):
        raise ValueError('Truncated PE optional header')
    return checksum, security


def verify(unsigned: bytes, signed: bytes) -> None:
    checksum, security = pe_fields(unsigned)
    if pe_fields(signed) != (checksum, security):
        raise ValueError('Signing changed the PE layout')
    if struct.unpack_from('<II', unsigned, security) != (0, 0):
        raise ValueError('Expected a freshly built unsigned executable')
    offset, size = struct.unpack_from('<II', signed, security)
    if offset != ((len(unsigned) + 7) & ~7) or size < 8 or offset + size != len(signed):
        raise ValueError('Unexpected Authenticode certificate table layout')
    if any(signed[len(unsigned):offset]):
        raise ValueError('Nonzero certificate alignment padding')
    before, after = bytearray(unsigned), bytearray(signed[:len(unsigned)])
    for start, length in ((checksum, 4), (security, 8)):
        before[start:start + length] = after[start:start + length] = bytes(length)
    if before != after:
        raise ValueError('Signing changed executable content')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('unsigned', type=Path)
    parser.add_argument('signed', type=Path)
    args = parser.parse_args()
    verify(args.unsigned.read_bytes(), args.signed.read_bytes())
    print('Executable content preserved by signing')
