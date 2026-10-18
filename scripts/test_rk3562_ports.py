#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Exercise model matching, boot boundaries and writes using temporary files."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ports_updater", ROOT / "tools/ports/uboot/update_uboot.py")
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)


class PortsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = json.loads((ROOT / "tools/ports/uboot/devices.json").read_text())

    def test_only_an_exact_unique_compatible_model_is_accepted(self):
        self.assertEqual(ports.choose_device(self.config, "Xifan NGP45H", ["rockchip,rk3562"])["id"], "ngp45h")
        for model, compat in (("NGP45H", ["rockchip,rk3562"]), ("Xifan NGP45H", ["rockchip,rk3566"])):
            with self.assertRaises(ValueError):
                ports.choose_device(self.config, model, compat)
        self.config["devices"] *= 2
        with self.assertRaises(ValueError):
            ports.choose_device(self.config, "Xifan NGP45H", ["rockchip,rk3562"])

    def test_image_path_and_layout_are_checked(self):
        for key, value in (("image", "../other.bin"), ("offset", 0), ("size", 16 * 1024 * 1024)):
            config = json.loads(json.dumps(self.config))
            config["devices"][0][key] = value
            with self.assertRaises(ValueError):
                ports.choose_device(config, "Xifan NGP45H", ["rockchip,rk3562"])

    def test_target_follows_flash_mount_and_earliest_partition(self):
        sys_block = self.root / "sys"
        disk = sys_block / "mmcblk7"
        (disk / "queue").mkdir(parents=True)
        (disk / "ro").write_text("0")
        (disk / "queue/logical_block_size").write_text("512")
        for number, start in ((1, 32768), (2, 50000)):
            partition = sys_block / f"mmcblk7p{number}"
            partition.mkdir()
            (partition / "start").write_text(str(start))
        target, boundary = ports.boot_target("/dev/mmcblk7p2 /flash vfat ro 0 0\n", sys_block, self.root)
        self.assertEqual(target, self.root / "mmcblk7")
        self.assertEqual(boundary, 16 * 1024 * 1024)
        (disk / "ro").write_text("1")
        with self.assertRaises(ValueError):
            ports.boot_target("/dev/mmcblk7p2 /flash vfat ro 0 0\n", sys_block, self.root)
        with self.assertRaises(ValueError):
            ports.boot_target("/dev/sda1 /flash vfat ro 0 0\n", sys_block, self.root)

    def fixture(self):
        disk, image = self.root / "card.bin", self.root / "image.bin"
        original, replacement = bytes(range(256)) * 256, b"replacement-data" * 256
        disk.write_bytes(original)
        image.write_bytes(replacement)
        return {"target": str(disk), "image": str(image), "offset": 32768,
                "size": len(replacement), "sha256": hashlib.sha256(replacement).hexdigest()}, original, replacement

    def test_flash_backs_up_then_writes_only_the_selected_range(self):
        plan, original, replacement = self.fixture()
        backup = ports.flash(plan, self.root / "backup")
        end = plan["offset"] + plan["size"]
        self.assertEqual(backup.read_bytes(), original[:end])
        self.assertEqual(Path(plan["target"]).read_bytes(), original[:32768] + replacement + original[end:])

    def test_changed_image_aborts_before_dd_and_preserves_backup(self):
        plan, original, _ = self.fixture()
        Path(plan["image"]).write_bytes(b"corrupt")
        with patch.object(ports.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "changed after validation"):
                ports.flash(plan, self.root / "backup")
            run.assert_not_called()
        self.assertEqual(Path(plan["target"]).read_bytes(), original)
        self.assertTrue((self.root / "backup/boot-front-before.bin").is_file())

    def test_dd_failure_is_not_reported_as_success(self):
        plan, original, _ = self.fixture()
        with patch.object(ports.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "dd")):
            with self.assertRaises(subprocess.CalledProcessError):
                ports.flash(plan, self.root / "backup")
        self.assertEqual(Path(plan["target"]).read_bytes(), original)
        self.assertTrue((self.root / "backup/boot-front-before.bin").is_file())

    def test_readback_mismatch_preserves_backup_and_fails(self):
        plan, _, _ = self.fixture()
        run = subprocess.run
        def corrupt_readback(args, **kwargs):
            if "iflag=direct,fullblock" in args:
                return subprocess.CompletedProcess(args, 0, stdout=b"wrong")
            return run(args, **kwargs)
        with patch.object(ports.subprocess, "run", side_effect=corrupt_readback):
            with self.assertRaisesRegex(ValueError, "read-back SHA-256"):
                ports.flash(plan, self.root / "backup")
        self.assertTrue((self.root / "backup/boot-front-before.bin").is_file())

    def test_screens_wait_for_three_seconds(self):
        display = ports.Display(self.root, None)
        display.shown_at = 10.0
        display.history = [{"state": "updating"}]
        with patch.object(ports.time, "monotonic", side_effect=[11.0, 13.0]), \
                patch.object(ports.time, "sleep") as sleep:
            display.hold()
        sleep.assert_called_once_with(2.0)
        self.assertEqual(display.history[0]["display_seconds"], 3.0)


if __name__ == "__main__":
    unittest.main()
