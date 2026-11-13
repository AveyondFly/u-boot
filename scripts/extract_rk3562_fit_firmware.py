#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Verify an RK3562 FIT backup and wrap its BL31 payloads for decode_bl31.py.

The generated ELF is a packaging container, not the original link-time ELF.
Only PT_LOAD addresses and bytes are used by U-Boot's decode_bl31.py.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from rk3562_control_dtb import read_tree


def prop(image, node, name, kind="x"):
    return subprocess.check_output(
        ["fdtget", "-t", kind, str(image), node, name], text=True
    ).strip()


def integer(image, node, name):
    value = 0
    for word in prop(image, node, name).split():
        value = (value << 32) | int(word, 16)
    return value


def verified_fit_payload(image, name):
    """Read an authenticated, uncompressed FIT payload in either external layout."""
    blob = image.read_bytes()
    tree = read_tree(image)
    node = "/images/" + name
    props = tree[node]
    if props.get("compression", b"none\0") != b"none\0":
        raise ValueError("compressed firmware is not supported")
    if "data" in props:
        if "data-offset" in props or "data-position" in props:
            raise ValueError("ambiguous FIT payload layout")
        payload = props["data"]
    else:
        if ("data-offset" in props) == ("data-position" in props):
            raise ValueError("expected one external FIT payload layout")
        header = struct.unpack_from(">I", blob, 4)[0]
        offset = (int.from_bytes(props["data-position"], "big") if "data-position" in props
                  else ((header + 3) & ~3) + int.from_bytes(props["data-offset"], "big"))
        size = int.from_bytes(props["data-size"], "big")
        if offset < header or size <= 0 or offset + size > len(blob):
            raise ValueError("firmware lies outside the FIT: " + name)
        payload = blob[offset:offset + size]
    hashes = [p for n, p in tree.items() if n.rsplit("/", 1)[0] == node
              and n.rsplit("/", 1)[1].startswith("hash")]
    if not hashes or any(p.get("algo") != b"sha256\0" or
                         p.get("value") != hashlib.sha256(payload).digest() for p in hashes):
        raise ValueError("FIT hash mismatch: " + name)
    return payload


def extract(image, output, soc="rk3562", require_optee=True):
    if soc not in ("rk3562", "rk3566"):
        raise ValueError("unsupported firmware SoC")
    blob = image.read_bytes()
    if blob[:4] != bytes.fromhex("d00dfeed"):
        raise ValueError("input must start with a FIT")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    segments = []
    names = subprocess.check_output(
        ["fdtget", "-l", str(image), "/images"], text=True
    ).split()
    atf_names = sorted((name for name in names if name.startswith("atf-")),
                       key=lambda name: int(name[4:]))
    if not atf_names or ("optee" in names) != require_optee:
        raise ValueError("expected ATF segments and OP-TEE in RK3562 FIT")
    for name in atf_names + (["optee"] if require_optee else []):
        node = "/images/" + name
        data = verified_fit_payload(image, name)
        size = len(data)
        load = integer(image, node, "load")
        actual = hashlib.sha256(data).hexdigest()
        (output / (name + ".bin")).write_bytes(data)
        records.append(dict(name=name, load=hex(load), size=size, sha256=actual))
        if name.startswith("atf-"):
            segments.append((load, data))
        elif load != 0x08400000:
            raise ValueError("unexpected RK3562 OP-TEE load address")

    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    elf = bytearray(struct.pack(
        "<16sHHIQQQIHHHHHH", ident, 2, 183, 1, segments[0][0],
        64, 0, 0, 64, 56, len(segments), 0, 0, 0
    ))
    elf.extend(bytes(56 * len(segments)))
    for index, (load, data) in enumerate(segments):
        offset = (len(elf) + 4095) & ~4095
        elf.extend(bytes(offset - len(elf)))
        struct.pack_into("<IIQQQQQQ", elf, 64 + 56 * index,
                         1, 7, offset, load, load, len(data), len(data), 4096)
        elf.extend(data)
    (output / (soc + "_bl31_vendor.elf")).write_bytes(elf)
    (output / "firmware.json").write_text(json.dumps(records, indent=2) + "\n")
    for record in records:
        print("{name}: load={load}, size={size}, sha256={sha256}".format(**record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    extract(args.image, args.output)


if __name__ == "__main__":
    main()
