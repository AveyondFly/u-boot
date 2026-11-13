#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Read X35H boot-stage diagnostics from a saved boot area or the SSH device."""
import argparse
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
TRACE_ID = 0xff00
VENDOR_OFFSET = 0x380000
SLOT_SIZE = 65536
DATA_OFFSET = 1024


def extract_boot_trace(blob):
    valid = []
    for index in range(4):
        offset = VENDOR_OFFSET + index * SLOT_SIZE
        slot = blob[offset:offset + SLOT_SIZE]
        if len(slot) != SLOT_SIZE:
            continue
        tag, version, next_index, count = struct.unpack_from('<IIHH', slot)
        if tag != 0x524b5644 or not version or count > 126 or next_index >= 4:
            continue
        if struct.unpack_from('<I', slot, SLOT_SIZE - 4)[0] != version:
            continue
        valid.append((version, slot, count))
    if not valid:
        raise ValueError('No valid vendor-storage record in this boot area')
    _, slot, count = max(valid, key=lambda item: item[0])
    for index in range(count):
        item, offset, size, flags = struct.unpack_from('<4H', slot, 16 + index * 8)
        if item != TRACE_ID:
            continue
        start = DATA_OFFSET + offset
        if not size or start + size > SLOT_SIZE - 8:
            raise ValueError('Boot trace lies outside vendor-storage data')
        trace = slot[start:start + size].rstrip(b'\0').decode('utf-8')
        if not trace.startswith('RK3566-BOOT-TRACE v1\n'):
            raise ValueError('Unknown boot trace format')
        return trace
    raise ValueError('No RK3566 boot trace in the latest vendor-storage record')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--image', type=Path, help='saved whole first 16 MiB, including sector zero')
    source.add_argument('--host', help='read /dev/mmcblk1 on the explicitly selected SSH device')
    args = parser.parse_args()
    if args.image:
        blob = args.image.read_bytes()
    else:
        sys.path.insert(0, str(ROOT))
        from ssh_client import SSHSession
        with SSHSession(args.host, 'root', strict_host_key=False, timeout=30) as session:
            with session.sftp() as sf:
                with sf.open('/dev/mmcblk1', 'rb') as stream:
                    blob = stream.read(16 * 1024 * 1024)
    print(extract_boot_trace(blob))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit('Boot trace unavailable: ' + str(error))
