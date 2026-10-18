#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Save read-only RK3562 vendor inputs over SFTP; never write to the device."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssh_client import SSHSession  # noqa: E402


def capture_file(sftp, remote, destination, offset=0, size=None):
    with sftp.open(remote, "rb") as source, destination.open("wb") as target:
        source.seek(offset)
        remaining = size
        while remaining is None or remaining > 0:
            block = source.read(min(65536, remaining) if remaining is not None else 65536)
            if not block:
                if remaining:
                    raise ValueError("short read from " + remote)
                break
            target.write(block)
            if remaining is not None:
                remaining -= len(block)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key")
    parser.add_argument("--insecure", action="store_true", help="allow an unverified host key")
    parser.add_argument("--vendor-dtb", required=True, help="remote vendor Linux DTB path")
    parser.add_argument("--idb-device", required=True, help="explicit Linux device containing RKNS")
    parser.add_argument("--idb-offset", type=lambda s: int(s, 0), default=64 * 512)
    parser.add_argument("--idb-size", type=lambda s: int(s, 0), default=512 * 1024)
    parser.add_argument("--uboot-device", required=True, help="explicit U-Boot partition/device")
    parser.add_argument("--uboot-offset", type=lambda s: int(s, 0), default=0)
    parser.add_argument("--uboot-size", type=lambda s: int(s, 0), default=4 * 1024 * 1024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.board):
        parser.error("invalid board id")
    if min(args.idb_offset, args.uboot_offset) < 0 or min(args.idb_size, args.uboot_size) <= 0:
        parser.error("offsets must be nonnegative and sizes must be positive")
    output = (args.output or ROOT / "board-data/rk3562" / args.board).resolve()
    if output.exists():
        parser.error("snapshot destination already exists; use a new --output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=output.parent) as tmp:
        stage = Path(tmp) / "snapshot"
        stage.mkdir()
        ranges = {
            "linux-vendor.dtb": (args.vendor_dtb, 0, None),
            "idb-original.bin": (args.idb_device, args.idb_offset, args.idb_size),
            "uboot-original.img": (args.uboot_device, args.uboot_offset, args.uboot_size),
        }
        with SSHSession(args.host, args.user, port=args.port,
                        password=os.environ.get("SSH_PASSWORD"), key_filename=args.key,
                        strict_host_key=not args.insecure, timeout=30) as session:
            with session.sftp() as sftp:
                for name, (remote, offset, size) in ranges.items():
                    capture_file(sftp, remote, stage / name, offset, size)
        if (stage / "linux-vendor.dtb").read_bytes()[:4] != bytes.fromhex("d00dfeed"):
            raise ValueError("vendor DTB does not start with FDT magic")
        if (stage / "idb-original.bin").read_bytes()[:4] != b"RKNS":
            raise ValueError("IDB range does not start with RKNS; check device/offset")
        if (stage / "uboot-original.img").read_bytes()[:4] != bytes.fromhex("d00dfeed"):
            raise ValueError("U-Boot range does not start with FIT; check device/offset")
        metadata = {
            "schema_version": 1, "board_id": args.board, "host": args.host,
            "captured_at": datetime.now(timezone.utc).isoformat(), "remote_ranges": ranges,
            "sha256": {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in ranges},
        }
        (stage / "snapshot.json").write_text(json.dumps(metadata, indent=2) + "\n")
        stage.rename(output)
    print("Read-only vendor snapshot saved: " + str(output))


if __name__ == "__main__":
    main()
