#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Extract and verify a payload from a Rockchip RKNS (IDB v2) image."""

import argparse
import hashlib
import struct
import sys
from pathlib import Path


SECTOR_SIZE = 512
HEADER_SIZE = 4 * SECTOR_SIZE
IMAGE_ENTRY_OFFSET = 120
IMAGE_ENTRY_SIZE = 88
MAX_IMAGES = 4


def die(message):
    raise ValueError(message)


def digest_info(boot_flag):
    hash_type = boot_flag & 0xF
    if hash_type == 1:
        return hashlib.sha256, 32, "sha256"
    if hash_type == 2:
        return hashlib.sha512, 64, "sha512"
    die("unsupported RKNS hash type: {}".format(hash_type))


def extract(input_path, output_path, image_index):
    if input_path.resolve() == output_path.resolve():
        die("input and output paths must differ")
    data = input_path.read_bytes()
    if len(data) < HEADER_SIZE:
        die("input is smaller than the 2048-byte RKNS header")
    if data[:4] != b"RKNS":
        die("input does not start with an RKNS header")

    size_and_nimage, boot_flag = struct.unpack_from("<II", data, 8)
    image_count = size_and_nimage >> 16
    header_hash_offset = (size_and_nimage & 0xFFFF) * 4
    if not 1 <= image_count <= MAX_IMAGES:
        die("invalid RKNS image count: {}".format(image_count))
    if not 0 <= image_index < image_count:
        die("image index {} is outside 0..{}".format(
            image_index, image_count - 1))

    digest, digest_size, digest_name = digest_info(boot_flag)
    entries_end = IMAGE_ENTRY_OFFSET + image_count * IMAGE_ENTRY_SIZE
    if header_hash_offset < entries_end:
        die("header hash overlaps the RKNS image table")
    if header_hash_offset + digest_size > HEADER_SIZE:
        die("header hash is outside the RKNS header")
    expected_header_hash = data[
        header_hash_offset:header_hash_offset + digest_size
    ]
    actual_header_hash = digest(data[:header_hash_offset]).digest()
    if actual_header_hash != expected_header_hash:
        die("RKNS header {} mismatch".format(digest_name))

    entries = []
    for index in range(image_count):
        entry_offset = IMAGE_ENTRY_OFFSET + index * IMAGE_ENTRY_SIZE
        size_and_offset = struct.unpack_from("<I", data, entry_offset)[0]
        sector_count = size_and_offset >> 16
        sector_offset = size_and_offset & 0xFFFF
        byte_offset = sector_offset * SECTOR_SIZE
        byte_size = sector_count * SECTOR_SIZE
        byte_end = byte_offset + byte_size
        if not sector_count:
            die("image {} has zero length".format(index))
        if byte_offset < HEADER_SIZE or byte_end > len(data):
            die("image {} is outside the input file".format(index))

        expected_hash = data[
            entry_offset + 24:entry_offset + 24 + digest_size
        ]
        payload = data[byte_offset:byte_end]
        actual_hash = digest(payload).digest()
        if actual_hash != expected_hash:
            die("image {} {} mismatch".format(index, digest_name))
        entries.append((byte_offset, byte_size, actual_hash.hex()))

    byte_offset, byte_size, payload_hash = entries[image_index]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data[byte_offset:byte_offset + byte_size])

    print("RKNS header: {} images, {} verified".format(
        image_count, digest_name))
    for index, (offset, size, image_hash) in enumerate(entries):
        print("image {}: offset={} size={} {}={}".format(
            index, offset, size, digest_name, image_hash))
    print("extracted image {} to {}".format(image_index, output_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path,
                        help="RKNS IDB image/area dump beginning with RKNS")
    parser.add_argument("output", type=Path, help="output payload file")
    parser.add_argument("--image", type=int, default=0,
                        help="payload index to extract (default: 0, DDR)")
    args = parser.parse_args()

    try:
        extract(args.input, args.output, args.image)
    except (OSError, ValueError) as exc:
        parser.exit(1, "error: {}\n".format(exc))


if __name__ == "__main__":
    sys.exit(main())
