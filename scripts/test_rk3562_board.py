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
from rk3562_control_dtb import read_tree, verify_power_configuration


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

    def test_new_boards_keep_screen_off_charging_and_their_own_batteries(self):
        for board, capacity, qmax, resistance, voltage, current in (
                ("rg43h", 2906, 3197, 102, 4280, 1500),
                ("rg43v", 2906, 3197, 102, 4280, 1500),
                ("rg52mini", 4654, 5119, 78, 4300, 2000),
                ("xf55h", 3727, 4100, 217, 4350, 2000)):
            with self.subTest(board=board):
                self.check_screen_off_charging(board, capacity, qmax, resistance, voltage, current)

    def check_screen_off_charging(self, board, capacity, qmax, resistance, voltage, current):
        profile_path = boards.PROFILES / board / "board.json"
        profile = boards.load_profile(profile_path)
        source = self.data / (board + ".dts")
        source.write_text('''/dts-v1/;
/ {
    pinctrl {
        gpio3: gpio@ffac0000 { gpio-controller; #gpio-cells = <2>; };
        pcfg_pull_none: pull-none { bias-disable; };
    };
    power-leds {
        compatible = "gpio-leds";
        led-red { gpios = <&gpio3 1 0>; };
        led-green { gpios = <&gpio3 0 0>; };
    };
    charge-animation { compatible = "rockchip,uboot-charge";
        rockchip,uboot-charge-on = <0>; };
    own-rail { regulator-name = "own-rail";
        regulator-min-microvolt = <2700000>;
        regulator-max-microvolt = <2700000>; };
    i2c@ff200000 { pmic@20 { compatible = "rockchip,rk817";
        battery { design_capacity = <3727>; design_qmax = <4100>;
            bat_res = <217>; ocv_table = <3400 3571 4174>; };
        charger { max_chrg_voltage = <4350>; max_chrg_current = <2000>; };
    }; };
};'''.replace("<3727>", f"<{capacity}>").replace("<4100>", f"<{qmax}>")
            .replace("<217>", f"<{resistance}>").replace("<4350>", f"<{voltage}>")
            .replace("<2000>", f"<{current}>"))
        base, overlay, output = [self.root / n for n in ("xf.dtb", "xf.dtbo", "xf-out.dtb")]
        for src, dst in ((source, base), (profile_path.parent / profile["overlays"][0], overlay)):
            subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(dst), str(src)],
                           check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["fdtoverlay", "-i", str(base), "-o", str(output), str(overlay)], check=True)
        boards.check_properties(output, profile["output_checks"])
        tree = read_tree(output)
        self.assertEqual(tree["/charge-animation"]["rockchip,uboot-charge-on"], b"\0\0\0\1")
        self.assertEqual(tree["/charge-animation"]["rockchip,uboot-exit-charge-auto"], bytes(4))
        self.assertIn("rockchip,screen-off-at-start", tree["/charge-animation"])
        for node in ("/i2c@ff200000/pmic@20/battery", "/i2c@ff200000/pmic@20/charger"):
            for key, value in read_tree(base)[node].items():
                self.assertEqual(tree[node][key], value)
        verify_power_configuration(base, output)
        # The screen-off charge policy must stay aligned with NGP45H while
        # each model keeps its own battery data and GPIO3_A0/A1 LED wiring.
        ngp_overlay, ngp_output = self.root / "ngp.dtbo", self.root / "ngp-out.dtb"
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(ngp_overlay),
                        str(boards.PROFILES / "ngp45h/charge.dtso")],
                       check=True, stderr=subprocess.DEVNULL)
        subprocess.run(["fdtoverlay", "-i", str(base), "-o", str(ngp_output),
                        str(ngp_overlay)], check=True)

        def policy(props):
            return {k: v for k, v in props.items()
                    if k not in ("rockchip,charging-led", "rockchip,charged-led")}

        self.assertEqual(policy(tree["/charge-animation"]),
                         policy(read_tree(ngp_output)["/charge-animation"]))
        self.assertIn("LED_GPIO", profile["required_kconfig"])

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
