#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Restore X35H's saved working TF boot region, preserving the partition table/data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from read_rk3566_boot_trace import extract_boot_trace

FRONT_SIZE = 16 * 1024 * 1024
OFFSET = 32768
REFERENCE_SHA256 = 'e0bfa261d7c35d61e81dfa9da731e4714f780349cbe2e1fdb27e74a9b451cf62'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def restore(device, reference, backup_dir, write=False, *, regular_fixture=False):
    original = reference.read_bytes()
    if len(original) != FRONT_SIZE or digest(original) != REFERENCE_SHA256:
        raise ValueError('Reference is not the saved, known-working X35H TF snapshot')
    device = device.resolve()
    info = device.stat()
    if not stat.S_ISBLK(info.st_mode) and not (regular_fixture and stat.S_ISREG(info.st_mode)):
        raise ValueError('Device must be a block device')
    if not regular_fixture:
        # Mounted partitions are not needed for this operation; fail safely.
        for line in Path('/proc/mounts').read_text().splitlines():
            mounted = Path(line.split()[0]).resolve()
            if mounted == device or (
                    mounted.parent == device.parent and mounted.name.startswith(device.name)
                    and mounted.name[len(device.name):].isdigit()):
                raise ValueError('Unmount this card before recovery: ' + str(mounted))
    with device.open('r+b' if write else 'rb', buffering=0) as target:
        current = target.read(FRONT_SIZE)
        if len(current) != FRONT_SIZE:
            raise ValueError('Short boot-region read')
        if current[:OFFSET] != original[:OFFSET]:
            raise ValueError('Partition table / first 32 KiB differs from the saved TF card; refusing')
        report = dict(device=str(device), bytes=FRONT_SIZE - OFFSET, offset=OFFSET,
                      before_sha256=digest(current), reference_sha256=digest(original), written=False)
        if not write:
            return report
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup = backup_dir / 'before-first16m.bin'
        with backup.open('xb') as saved:
            saved.write(current)
            saved.flush()
            os.fsync(saved.fileno())
        if digest(backup.read_bytes()) != digest(current):
            raise ValueError('Backup read-back verification failed')
        (backup_dir / 'before.json').write_text(json.dumps(report, indent=2) + '\n')
        # Diagnostic extraction must never prevent restoring the known-good boot.
        try:
            trace = extract_boot_trace(current)
        except (ValueError, UnicodeError):
            trace = None
        if trace:
            trace_path = backup_dir / 'boot-trace-before.txt'
            trace_path.write_text(trace)
            report['boot_trace'] = str(trace_path)
        target.seek(OFFSET)
        payload = memoryview(original)[OFFSET:]
        while payload:
            count = target.write(payload)
            if not count:
                raise OSError('Short recovery write; retain the backup')
            payload = payload[count:]
        target.flush()
        os.fsync(target.fileno())
        target.seek(0)
        after = target.read(FRONT_SIZE)
        if after != original:
            raise ValueError('Recovery read-back differs; retain the backup')
        report.update(written=True, after_sha256=digest(after), backup=str(backup))
        (backup_dir / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, type=Path)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--write', action='store_true', help='without this flag, check only')
    args = parser.parse_args()
    backup = args.backup_dir or Path(__file__).resolve().parents[1] / (
        'board-data/rk3566/powkiddy-x35h/recovery-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    print(json.dumps(restore(args.device, args.reference, backup, args.write), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('X35H recovery failed: ' + str(error))
