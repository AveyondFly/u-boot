#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Check all-board Ports ZIP completeness, provenance and extraction layout."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import package_rk3562_ports as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / "source"
        shutil.copytree(package.ROOT / "tools/ports", source / "tools/ports")
        self.config_path = source / "tools/ports/uboot/devices.json"
        registered = {d["id"] for d in json.loads(self.config_path.read_text())["devices"]}
        self.boards = ["ngp45h", "pocket-ais"] + sorted(registered - {"ngp45h", "pocket-ais"})
        patcher = patch.object(package, "ROOT", source)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(package, "screens", side_effect=self.screens)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.builds = []
        for board in self.boards:
            build = self.root / board
            build.mkdir()
            image = build / "rk3562-uboot-16m.bin"
            with image.open("wb") as stream:
                stream.write(board.encode())
                stream.truncate(16 * 1024 * 1024 - 32768)
            mmc = {"packed_spl": {"control_dtb_verified": True},
                   "stages": {stage: {"tf1_before_emmc": True} for stage in ("spl", "uboot")}}
            (build / "board-mmc.json").write_text(json.dumps(mmc))
            (build / "board-power.json").write_text('{"nodes": 1}')
            metadata = dict(board_id=board, dtb_only=False, dtb_mode="embedded",
                            source_commit="a" * 40,
                            tracked_source_diff_sha256=hashlib.sha256(b"").hexdigest(),
                            artifacts={name: package.sha256(build / name) for name in
                                       (image.name, "board-mmc.json", "board-power.json")})
            (build / "board-build.json").write_text(json.dumps(metadata))
            self.builds.append(build)
        self.output = self.root / "ports"

    @staticmethod
    def screens(output, font):
        output.mkdir()
        for name in ("updating", "success", "failure"):
            (output / (name + ".png")).write_bytes(b"screen fixture")

    def build(self, builds=None, **kwargs):
        return package.build_ports(builds or self.builds, self.output, self.root / "font", **kwargs)

    def metadata(self, index, **changes):
        path = self.builds[index] / "board-build.json"
        metadata = json.loads(path.read_text())
        metadata.update(changes)
        path.write_text(json.dumps(metadata))

    def test_all_boards_archive_extracts_directly_into_ports(self):
        metadata = self.build(require_all=True, require_clean=True, source_commit="a" * 40,
                              version="1.2.3", release_date="2026-10-19")
        self.assertEqual(metadata["boards"], sorted(self.boards))
        archive = package.archive_ports(self.output, self.root / "dist")
        self.assertEqual(archive.name, "rk3562-update-uboot-ports-2026-10-19-v1.2.3.zip")
        self.assertTrue(Path(str(archive) + ".sha256").read_text().startswith(package.sha256(archive)))
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            self.assertIn("Update U-Boot.sh", names)
            self.assertIn("uboot/update_uboot.py", names)
            self.assertTrue(all(name == "Update U-Boot.sh" or name.startswith("uboot/") for name in names))
            self.assertTrue(bundle.getinfo("Update U-Boot.sh").external_attr >> 16 & 0o111)
            devices = json.loads(bundle.read("uboot/devices.json"))["devices"]
            for device in devices:
                image = bundle.read("uboot/" + device["image"])
                self.assertEqual(hashlib.sha256(image).hexdigest(), device["sha256"])
                self.assertIn("uboot/" + device["id"] + "-board-mmc.json", names)
            for line in bundle.read("uboot/SHA256SUMS").decode().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(bundle.read(name)).hexdigest(), digest)

    def test_subset_manifest_and_rebuild_remove_unpacked_devices(self):
        self.build()
        self.build([self.builds[0]], expected_boards=["ngp45h"])
        data = self.output / "uboot"
        self.assertEqual([d["id"] for d in json.loads((data / "devices.json").read_text())["devices"]], ["ngp45h"])
        self.assertFalse((data / "pocket-ais-uboot.bin").exists())

    def test_incomplete_matrix_or_registration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "all board profiles"):
            self.build([self.builds[0]], require_all=True)
        with self.assertRaisesRegex(ValueError, "requested board matrix"):
            self.build(expected_boards=["ngp45h"])
        config = json.loads(self.config_path.read_text())
        config["devices"].pop()
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "registrations must match"):
            self.build(require_all=True)

    def test_mixed_revisions_external_and_dirty_ci_builds_are_rejected(self):
        self.metadata(1, source_commit="b" * 40)
        with self.assertRaisesRegex(ValueError, "exactly one source revision"):
            self.build()
        self.metadata(1, source_commit="a" * 40, dtb_mode="external")
        with self.assertRaisesRegex(ValueError, "embedded full build"):
            self.build()
        self.metadata(1, dtb_mode="embedded", tracked_source_diff_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "clean source tree"):
            self.build(require_clean=True)
        self.metadata(1, tracked_source_diff_sha256=hashlib.sha256(b"").hexdigest())
        with self.assertRaisesRegex(ValueError, "requested revision"):
            self.build(source_commit="c" * 40)

    def test_corrupt_image_or_validation_report_preserves_previous_output(self):
        self.build()
        original = (self.output / "uboot/package.json").read_bytes()
        for name in ("rk3562-uboot-16m.bin", "board-mmc.json"):
            path = self.builds[0] / name
            data = path.read_bytes()
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "artifact checksum mismatch"):
                self.build()
            self.assertEqual((self.output / "uboot/package.json").read_bytes(), original)
            path.write_bytes(data)

    def test_unverified_spl_and_duplicate_models_are_rejected(self):
        path = self.builds[0] / "board-mmc.json"
        mmc = json.loads(path.read_text())
        mmc["packed_spl"]["control_dtb_verified"] = False
        path.write_text(json.dumps(mmc))
        metadata = json.loads((self.builds[0] / "board-build.json").read_text())
        metadata["artifacts"][path.name] = package.sha256(path)
        self.metadata(0, artifacts=metadata["artifacts"])
        with self.assertRaisesRegex(ValueError, "packed SPL checks must pass"):
            self.build()
        mmc["packed_spl"]["control_dtb_verified"] = True
        path.write_text(json.dumps(mmc))
        metadata["artifacts"][path.name] = package.sha256(path)
        self.metadata(0, artifacts=metadata["artifacts"])
        config = json.loads(self.config_path.read_text())
        config["devices"][1]["models"] = config["devices"][0]["models"]
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "models must be nonempty and unique"):
            self.build()


if __name__ == "__main__":
    unittest.main()
