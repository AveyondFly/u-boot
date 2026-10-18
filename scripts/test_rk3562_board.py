#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Host regression tests for RK3562 board isolation and firmware validation."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_rk3562_board as boards
from capture_rk3562_board import capture_file
from extract_rk3562_fit_firmware import extract as extract_fit


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "vendor"
        self.data.mkdir()
        self.profile_path = self.root / "profile/board.json"
        self.profile_path.parent.mkdir()
        base = self.data / "base.dts"
        base.write_text('''/dts-v1/;
/ { compatible = "test,board", "rockchip,rk3562";
    gpio3: gpio { gpio-controller; #gpio-cells = <2>; phandle = <5>; };
    battery { red = <&gpio3 18 0>; };
    charge-animation { compatible = "rockchip,uboot-charge"; };
};''')
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o",
                        str(self.data / "base.dtb"), str(base)], check=True)
        self.overlay = self.profile_path.parent / "charge.dtso"
        self.overlay.write_text('''/dts-v1/; /plugin/;
/ { fragment@0 { target-path = "/charge-animation";
    __overlay__ { rockchip,charged-led-min-soc = <90>; };
}; };''')
        self.profile = {
            "schema_version": 1, "id": "fixture-a", "defconfig": "rk3562_charge_defconfig",
            "inputs": {"linux_dtb": "base.dtb"}, "overlays": ["charge.dtso"],
            "checks": [{"node": "/battery", "property": "red", "gpio_controller": "/gpio", "pin": 18}],
            "output_checks": [{"node": "/charge-animation", "property": "rockchip,charged-led-min-soc", "cells": [90]}],
        }
        self.profile_path.write_text(json.dumps(self.profile))

    def build(self, output, profile=None):
        with contextlib.redirect_stdout(io.StringIO()):
            boards.build(profile or self.profile, self.profile_path, self.data, output, "external", True)

    def test_multiple_boards_are_isolated(self):
        first, second = self.root / "out-a", self.root / "out-b"
        self.build(first)
        other = dict(self.profile, id="fixture-b")
        self.build(second, other)
        self.assertEqual(json.loads((first / "board-build.json").read_text())["board_id"], "fixture-a")
        self.assertEqual(json.loads((second / "board-build.json").read_text())["board_id"], "fixture-b")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.build(first, other)

    def test_failed_rebuild_preserves_previous_output(self):
        output = self.root / "out"
        self.build(output)
        before = (output / "board-build.json").read_bytes()
        self.overlay.write_text("invalid dts")
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(output)
        self.assertEqual((output / "board-build.json").read_bytes(), before)

    def test_wrong_board_gpio_is_rejected(self):
        self.profile["checks"][0]["pin"] = 17
        with self.assertRaisesRegex(ValueError, "board DTB mismatch"):
            self.build(self.root / "out")

    def test_snapshot_checksum_and_board_id(self):
        snapshot = self.data / "snapshot.json"
        snapshot.write_text(json.dumps({"board_id": "fixture-b", "sha256": {}}))
        with self.assertRaisesRegex(ValueError, "different board"):
            self.build(self.root / "out")
        snapshot.write_text(json.dumps({"board_id": "fixture-a", "sha256": {"base.dtb": "0" * 64}}))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.build(self.root / "out")

    def test_input_output_overlap_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be separate"):
            self.build(self.data / "output")

    def test_decompiled_dts_input(self):
        self.profile["inputs"] = {"linux_dts": "base.dts"}
        self.build(self.root / "out")

    def test_pocket_ais_uses_its_red_green_wiring_and_battery_data(self):
        profile_path = boards.PROFILES / "pocket-ais/board.json"
        profile = boards.load_profile(profile_path)
        source = self.data / "pocket.dts"
        source.write_text('''/dts-v1/;
/ {
    model = "Pocket AIS";
    compatible = "aislpc,pocket-ais", "rockchip,rk3562";
    pinctrl {
        gpio3: gpio@ffac0000 { gpio-controller; #gpio-cells = <2>; };
        charge-led {
            charge_led_gpio: charge-led-gpio { rockchip,pins = <3 0 0 0 3 1 0 0>; };
        };
    };
    i2c@ff200000 {
        pmic@20 {
            compatible = "rockchip,rk817";
            battery {
                charge_red_gpio = <&gpio3 1 0>;
                charge_green_gpio = <&gpio3 0 0>;
                design_capacity = <3675>; design_qmax = <4042>; bat_res = <107>;
            };
        };
    };
    charge-animation { compatible = "rockchip,uboot-charge"; };
};''')
        dtb = self.data / "linux-vendor.dtb"
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(dtb), str(source)],
                       check=True, stderr=subprocess.DEVNULL)
        output = self.root / "pocket-output"
        with contextlib.redirect_stdout(io.StringIO()):
            boards.build(profile, profile_path, self.data, output, "embedded", True)
        battery = "/i2c@ff200000/pmic@20/battery"
        self.assertEqual(boards.prop(output / "rk3562-charge.dtb", battery, "design_capacity"), "e5b")
        # NGP45H's GPIO3_C2/C1 pair must never pass as Pocket AIS wiring.
        subprocess.run(["fdtput", "-t", "x", str(dtb), battery, "charge_red_gpio",
                        boards.prop(dtb, "/pinctrl/gpio@ffac0000", "phandle"), "12", "0"], check=True)
        with self.assertRaisesRegex(ValueError, "board DTB mismatch"):
            boards.build(profile, profile_path, self.data, output, "embedded", True)

    def test_mode_config_and_board_overrides(self):
        self.profile["kconfig"] = {"DEFAULT_DEVICE_TREE": "rk3562-custom", "POWER_FG_RK817": False}
        path = self.root / "config"
        boards.render_config(self.profile, "embedded", path)
        text = path.read_text()
        self.assertIn('CONFIG_DEFAULT_DEVICE_TREE="rk3562-custom"\n', text)
        self.assertIn("CONFIG_EMBED_KERNEL_DTB_ALWAYS=y\n", text)
        self.assertIn("# CONFIG_ROCKCHIP_EARLY_DISTRO_DTB is not set\n", text)
        self.assertIn("# CONFIG_POWER_FG_RK817 is not set\n", text)
        boards.render_config(self.profile, "external", path)
        text = path.read_text()
        self.assertIn("CONFIG_ROCKCHIP_EARLY_DISTRO_DTB=y\n", text)
        self.assertIn("# CONFIG_EMBED_KERNEL_DTB is not set\n", text)

    def test_input_cannot_escape_board_directory(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            boards.input_file(self.data, "../elsewhere")

    def test_cli_defaults_to_embedded(self):
        with patch("sys.argv", ["build_rk3562_board.py", "--profile", str(self.profile_path)]), \
             patch.object(boards, "build") as mocked:
            boards.main()
        self.assertEqual(mocked.call_args.args[4], "embedded")

    def test_capture_reads_exact_range_and_detects_short_read(self):
        class SFTP:
            def open(self, path, mode):
                assert mode == "rb"
                return io.BytesIO(b"0123456789")
        destination = self.root / "capture.bin"
        capture_file(SFTP(), "/dev/example", destination, 3, 4)
        self.assertEqual(destination.read_bytes(), b"3456")
        with self.assertRaisesRegex(ValueError, "short read"):
            capture_file(SFTP(), "/dev/example", destination, 3, 20)

    def test_fit_with_variable_atf_count_and_corrupt_hash(self):
        # Two ATF segments, with both 32-bit and 64-bit load addresses.
        data = [b"test-atf-one", b"test-atf-two", b"test-optee"]
        names = ["atf-1", "atf-2", "optee"]
        loads = ["0x40000", "0 0xfe490000", "0 0x8400000"]
        nodes = []
        for index, (name, payload, load) in enumerate(zip(names, data, loads)):
            digest = " ".join(hashlib.sha256(payload).hexdigest()[i:i+2] for i in range(0, 64, 2))
            nodes.append(f'''{name} {{ compression = "none";
data-position = <{(index+1)*4096}>; data-size = <{len(payload)}>; load = <{load}>;
hash {{ algo = "sha256"; value = [{digest}]; }}; }};''')
        source = self.root / "fit.dts"
        source.write_text('/dts-v1/; / { images { ' + "".join(nodes) + ' }; };')
        image = self.root / "fit.img"
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(image), str(source)], check=True)
        blob = bytearray(image.read_bytes())
        for index, payload in enumerate(data):
            offset = (index+1)*4096
            blob.extend(bytes(max(0, offset - len(blob))))
            blob.extend(payload)
        image.write_bytes(blob)
        with contextlib.redirect_stdout(io.StringIO()):
            extract_fit(image, self.root / "firmware")
        self.assertEqual((self.root / "firmware/atf-2.bin").read_bytes(), data[1])
        blob[4096] ^= 1
        image.write_bytes(blob)
        with self.assertRaisesRegex(ValueError, "FIT hash mismatch"):
            extract_fit(image, self.root / "corrupt")


if __name__ == "__main__":
    unittest.main()
