#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Check RK3562 CI selection, archive boundaries and release integrity."""

import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import ci_rk3562 as ci


class CITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "vendor"
        self.data.mkdir()
        for name in ci.input_names(ci.profile_for("ngp45h")):
            (self.data / name).write_bytes(name.encode())
        (self.data / "private-diagnostics.txt").write_text("must stay local")

    def pack(self):
        return ci.pack_inputs("ngp45h", self.data, self.root / "archives")

    def test_matrix_discovers_boards_and_rejects_bad_selection(self):
        self.assertIn("ngp45h", ci.matrix("all")["board"])
        self.assertEqual(ci.matrix(" ngp45h "), {"board": ["ngp45h"]})
        for value in ("", "ngp45h,ngp45h", "../../outside"):
            with self.assertRaises(ValueError):
                ci.matrix(value)
        with self.assertRaises(OSError):
            ci.matrix("unknown-board")

    def test_vendor_round_trip_excludes_private_data(self):
        path = self.pack()
        destination = self.root / "unpacked"
        ci.unpack_inputs("ngp45h", path, destination)
        self.assertFalse((destination / "private-diagnostics.txt").exists())
        for name in ci.input_names(ci.profile_for("ngp45h")):
            self.assertEqual((self.data / name).read_bytes(), (destination / name).read_bytes())
        ci.verify_snapshot(destination, "ngp45h")
        with self.assertRaisesRegex(ValueError, "must be empty"):
            ci.unpack_inputs("ngp45h", path, destination)

    def test_corrupt_download_is_rejected(self):
        path = self.pack()
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            ci.unpack_inputs("ngp45h", path, self.root / "unpacked")

    def test_archive_paths_and_links_are_rejected(self):
        path = self.pack()
        for name, kind in (("../escaped", tarfile.REGTYPE), ("linux-vendor.dtb", tarfile.SYMTYPE)):
            with tarfile.open(path, "w:xz") as archive:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "/tmp/outside"
                archive.addfile(entry, io.BytesIO())
            ci.write_checksum(path)
            with self.assertRaisesRegex(ValueError, "unexpected vendor archive entry"):
                ci.unpack_inputs("ngp45h", path, self.root / "unpacked")
        self.assertFalse((self.root / "escaped").exists())

    def test_snapshot_board_and_payload_checksums_are_verified(self):
        path = self.pack()
        with tarfile.open(path) as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive}
        original = json.loads(files["snapshot.json"])
        for snapshot in (dict(original, board_id="other-board"),
                         dict(original, sha256={name: "0" * 64 for name in original["sha256"]})):
            files["snapshot.json"] = json.dumps(snapshot).encode()
            with tarfile.open(path, "w:xz") as archive:
                for name, data in files.items():
                    ci.add_bytes(archive, name, data)
            ci.write_checksum(path)
            with self.assertRaises(ValueError):
                ci.unpack_inputs("ngp45h", path, self.root / "unpacked")

    def test_release_bundle_rejects_tampered_or_wrong_board_build(self):
        build = self.root / "build"
        build.mkdir()
        names = ["uboot.img", "idblock.img", "rk3562-uboot-16m.bin", "u-boot.config"]
        for name in names:
            (build / name).write_bytes(name.encode())
        metadata = {"board_id": "ngp45h", "dtb_only": False, "dtb_mode": "embedded",
                    "source_commit": "a" * 40,
                    "artifacts": {name: ci.sha256(build / name) for name in names}}
        record = build / "board-build.json"
        record.write_text(json.dumps(metadata))
        archive = ci.package("ngp45h", build, self.root / "dist")
        with tarfile.open(archive) as bundle:
            self.assertEqual(len(bundle.getnames()), len(names) + 2)
            self.assertTrue(any(name.endswith("/SHA256SUMS") for name in bundle.getnames()))
        (build / "uboot.img").write_bytes(b"changed after build")
        with self.assertRaisesRegex(ValueError, "artifact checksum mismatch"):
            ci.package("ngp45h", build, self.root / "dist")
        metadata["board_id"] = "other-board"
        record.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "full build of the selected board"):
            ci.package("ngp45h", build, self.root / "dist")


if __name__ == "__main__":
    unittest.main()
