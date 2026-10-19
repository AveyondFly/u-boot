#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Regression checks for board DTB export and power configuration isolation."""

import json
import contextlib
import hashlib
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import build_rk3562_board as boards
from rk3562_control_dtb import power_configuration, read_tree, vendor_dtsi, verify_power_configuration
from rk3562_mmc import (BOOT_MMC, extract_vendor_trees, validate_boot_storage,
                        verify_mmc_configuration, verify_packed_spl,
                        write_mmc_include, write_spl_overlay)


class ControlDTBTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / "base.dts"
        source.write_text('''/dts-v1/;
/ {
    model = "fixture"; compatible = "fixture,board", "rockchip,rk3562";
    firmware {}; psci {};
    pinctrl {
        gpio0: gpio { gpio-controller; #gpio-cells = <2>; };
        sdmmc0 { sdmmc0_bus4: bus4 { rockchip,pins = <1 11 1 0>; }; };
    };
    source: source-regulator {
        compatible = "regulator-fixed"; regulator-name = "vcc_sys";
        regulator-min-microvolt = <3800000>; regulator-max-microvolt = <3800000>;
        regulator-always-on;
    };
    rail: sd-regulator {
        compatible = "regulator-gpio"; regulator-name = "vcc_sd";
        regulator-min-microvolt = <1800000>; regulator-max-microvolt = <3300000>;
        vin-supply = <&source>; gpios = <&gpio0 5 1>;
        regulator-state-mem { regulator-off-in-suspend; };
    };
    i2c { pmic@20 { compatible = "rockchip,rk817"; vcc1-supply = <&source>; }; };
    mmc { vmmc-supply = <&rail>; };
};''')
        self.base = self.root / "base.dtb"
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(self.base), str(source)],
                       check=True, stderr=subprocess.DEVNULL)

    def export(self):
        generated = self.root / "vendor.dtsi"
        vendor_dtsi(self.base, generated)
        source = self.root / "control.dts"
        source.write_text('/dts-v1/;\n/include/ "vendor.dtsi"\n')
        output = self.root / "control.dtb"
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(output), str(source)],
                       check=True, stderr=subprocess.DEVNULL)
        return output

    def test_export_preserves_data_and_restores_required_labels(self):
        output = self.export()
        before, after = read_tree(self.base), read_tree(output)
        for path, props in before.items():
            if path != "/__symbols__":
                for name, value in props.items():
                    self.assertEqual(value, after[path][name])
                self.assertLessEqual(after[path].keys() - props.keys(), {"phandle"})
        for label in ("firmware", "psci", "sdmmc0_pins"):
            self.assertIn(label, after["/__symbols__"])
        self.assertEqual(verify_power_configuration(self.base, output)["nodes"], 4)

    def test_board_voltage_and_gpio_changes_are_rejected(self):
        for property_name, values in (("regulator-min-microvolt", ["3300000"]),
                                      ("gpios", ["1", "7", "1"])):
            output = self.export()
            if property_name == "gpios":
                handle = int.from_bytes(read_tree(output)["/pinctrl/gpio"]["phandle"], "big")
                values[0] = str(handle)
            subprocess.run(["fdtput", "-t", "i", str(output), "/sd-regulator", property_name, *values], check=True)
            with self.assertRaisesRegex(ValueError, "board power configuration changed"):
                verify_power_configuration(self.base, output)

    def test_supply_comparison_uses_paths_instead_of_phandle_numbers(self):
        output = self.export()
        tree = read_tree(output)
        old = int.from_bytes(tree["/source-regulator"]["phandle"], "big")
        subprocess.run(["fdtput", "-t", "i", str(output), "/source-regulator", "phandle", str(old + 100)], check=True)
        subprocess.run(["fdtput", "-t", "i", str(output), "/sd-regulator", "vin-supply", str(old + 100)], check=True)
        subprocess.run(["fdtput", "-t", "i", str(output), "/i2c/pmic@20", "vcc1-supply", str(old + 100)], check=True)
        self.assertEqual(power_configuration(self.base), power_configuration(output))

    def test_pmic_input_supply_changes_are_rejected(self):
        output = self.export()
        other = int.from_bytes(read_tree(output)["/sd-regulator"]["phandle"], "big")
        subprocess.run(["fdtput", "-t", "i", str(output), "/i2c/pmic@20", "vcc1-supply", str(other)], check=True)
        with self.assertRaisesRegex(ValueError, "board power configuration changed"):
            verify_power_configuration(self.base, output)

    def test_model_export_requires_resolvable_symbols(self):
        subprocess.run(["fdtput", "-r", str(self.base), "/__symbols__"], check=True)
        with self.assertRaisesRegex(ValueError, "needs __symbols__"):
            vendor_dtsi(self.base, self.root / "missing.dtsi")

    def test_registered_boards_select_distinct_defconfigs_and_trees(self):
        configs, trees = set(), set()
        for board in ("ngp45h", "pocket-ais"):
            profile = boards.load_profile(boards.PROFILES / board / "board.json")
            config = self.root / (board + "_defconfig")
            boards.render_config(profile, "embedded", config)
            expected = profile["control_dtb"]["name"]
            self.assertIn('CONFIG_DEFAULT_DEVICE_TREE="' + expected + '"', config.read_text())
            self.assertNotEqual(expected, "rk3562-evb")
            configs.add(profile["defconfig"])
            trees.add(expected)
            profile["kconfig"] = {"DEFAULT_DEVICE_TREE": "rk3562-evb"}
            with self.assertRaisesRegex(ValueError, "must select its own"):
                boards.render_config(profile, "embedded", config)
        self.assertEqual(len(configs), 2)
        self.assertEqual(len(trees), 2)


class BootMMCTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.fixture("linux", modern=True)
        self.uboot = self.fixture("uboot")
        self.spl = self.fixture("spl", hs400=True)

    def compile(self, source, output):
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(output), str(source)],
                       check=True, stderr=subprocess.DEVNULL)
        return output

    def fixture(self, name, modern=False, hs400=False):
        # Different phandles and pin-container names must not hide pin/drive errors.
        clock, pins, config = (9, 10, 11) if modern else (101, 102, 103)
        pin_group = "sdmmc0" if modern else "sdmmc0-pins"
        hs = "mmc-hs400-1_8v; mmc-hs400-enhanced-strobe;" if hs400 else ""
        extra = "no-sdio; non-removable;" if modern else "fixed-emmc-driver-type = <1>;"
        rate = 200000000 if modern else 150000000
        source = self.root / (name + ".dts")
        source.write_text(f'''/dts-v1/;
/ {{
    aliases {{ mmc0 = "/mmc@ff870000"; mmc1 = "/mmc@ff880000"; }};
    chosen {{ u-boot,spl-boot-order = "/mmc@ff880000", "/mmc@ff870000"; }};
    clock: clock {{ #clock-cells = <1>; #reset-cells = <1>; phandle = <{clock}>; }};
    pinctrl {{
        config: pull-up {{ bias-pull-up; drive-strength = <2>; phandle = <{config}>; }};
        {pin_group} {{ pins: clk {{ rockchip,pins = <1 16 1 &config>; phandle = <{pins}>; }}; }};
    }};
    mmc@ff870000 {{ compatible = "rockchip,rk3562-dwcmshc";
        status = "okay"; max-frequency = <200000000>; bus-width = <8>;
        clocks = <&clock 153>; resets = <&clock 10>; {extra} {hs}
    }};
    mmc@ff880000 {{ compatible = "rockchip,rk3288-dw-mshc";
        status = "okay"; max-frequency = <{rate}>;
        clocks = <&clock 143>; resets = <&clock 20>;
        pinctrl-names = "default"; pinctrl-0 = <&pins>;
    }};
}};
''')
        return self.compile(source, source.with_suffix(".dtb"))

    def test_vendor_mmc_include_remaps_phandles_and_removes_linux_modes(self):
        include = self.root / "mmc.dtsi"
        write_mmc_include(self.base, self.uboot, include)
        source = self.root / "generated.dts"
        source.write_text((self.root / "linux.dts").read_text() +
                          '\n&{/mmc@ff870000} { mmc-hs400-1_8v; mmc-hs400-enhanced-strobe; };\n' +
                          '/include/ "mmc.dtsi"\n')
        result = self.compile(source, source.with_suffix(".dtb"))
        verify_mmc_configuration(self.uboot, result, BOOT_MMC)
        self.assertNotIn("mmc-hs400-1_8v", read_tree(result)[BOOT_MMC[0]])

    def test_frequency_pin_and_drive_changes_are_rejected(self):
        for node, prop, values in ((BOOT_MMC[1], "max-frequency", ["200000000"]),
                                    ("/pinctrl/sdmmc0-pins/clk", "rockchip,pins", ["1", "17", "1", "103"]),
                                    ("/pinctrl/pull-up", "drive-strength", ["3"])):
            candidate = self.root / "wrong.dtb"
            candidate.write_bytes(self.uboot.read_bytes())
            subprocess.run(["fdtput", "-t", "i", str(candidate), node, prop, *values], check=True)
            with self.assertRaisesRegex(ValueError, "MMC configuration changed"):
                verify_mmc_configuration(self.uboot, candidate, BOOT_MMC)

    def test_missing_pin_provider_is_rejected(self):
        subprocess.run(["fdtput", "-r", str(self.uboot), "/pinctrl/sdmmc0-pins/clk"], check=True)
        with self.assertRaisesRegex(ValueError, "unresolved MMC reference"):
            verify_mmc_configuration(self.spl, self.uboot, BOOT_MMC)

    def test_spl_overlay_preserves_different_hs400_settings(self):
        overlay = self.root / "spl.dtbo"
        write_spl_overlay(self.uboot, self.spl, overlay)
        candidate = self.root / "spl-output.dtb"
        subprocess.run(["fdtoverlay", "-i", str(self.uboot), "-o", str(candidate), str(overlay)], check=True)
        verify_mmc_configuration(self.spl, candidate, BOOT_MMC)
        self.assertNotEqual(candidate.read_bytes(), self.uboot.read_bytes())

    def test_unsupported_stage_difference_is_rejected(self):
        subprocess.run(["fdtput", "-t", "i", str(self.spl), BOOT_MMC[1], "max-frequency", "100000000"], check=True)
        with self.assertRaisesRegex(ValueError, "unsupported vendor SPL"):
            write_spl_overlay(self.uboot, self.spl, self.root / "spl.dtbo")

    def test_boot_order_and_stage_modes_are_verified(self):
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "u-boot.dtb").write_bytes(self.uboot.read_bytes())
        (artifacts / "u-boot-spl.dtb").write_bytes(self.spl.read_bytes())
        references = {"uboot": self.uboot, "spl": self.spl}
        self.assertTrue(validate_boot_storage(references, artifacts)["stages"]["spl"]["tf1_before_emmc"])
        subprocess.run(["fdtput", "-t", "s", str(artifacts / "u-boot.dtb"), "/chosen",
                        "u-boot,spl-boot-order", *BOOT_MMC], check=True)
        with self.assertRaisesRegex(ValueError, "prefer TF1"):
            validate_boot_storage(references, artifacts)

    def vendor_inputs(self):
        payloads = [bytes(512), b"SPL-CODE" + self.spl.read_bytes()]
        payloads = [data + bytes(-len(data) % 512) for data in payloads]
        header = bytearray(2048)
        header[:4] = b"RKNS"
        struct.pack_into("<II", header, 8, (2 << 16) | (512 // 4), 1)
        sector = 4
        for index, data in enumerate(payloads):
            entry = 120 + index * 88
            struct.pack_into("<I", header, entry, (len(data) // 512 << 16) | sector)
            header[entry + 24:entry + 56] = hashlib.sha256(data).digest()
            sector += len(data) // 512
        header[512:544] = hashlib.sha256(header[:512]).digest()
        idb = self.root / "idb.bin"
        idb.write_bytes(header + b"".join(payloads))
        payload = self.uboot.read_bytes()
        digest = hashlib.sha256(payload).digest().hex(" ")
        source = self.root / "fit.dts"
        source.write_text('/dts-v1/; / { images { fdt { compression = "none"; '
                          f'data-position = <4096>; data-size = <{len(payload)}>; '
                          f'hash {{ algo = "sha256"; value = [{digest}]; }}; '
                          '}; }; };')
        image = self.compile(source, self.root / "fit.img")
        image.write_bytes(image.read_bytes().ljust(4096, b"\0") + payload)
        return idb, image

    def test_vendor_dtb_extraction_verifies_idb_and_fit_hashes(self):
        idb, image = self.vendor_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            result = extract_vendor_trees(idb, image, self.root / "vendor")
        self.assertEqual(result["spl"].read_bytes(), self.spl.read_bytes())
        self.assertEqual(result["uboot"].read_bytes(), self.uboot.read_bytes())
        blob = bytearray(image.read_bytes())
        blob[-1] ^= 1
        image.write_bytes(blob)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "FIT hash mismatch"):
            extract_vendor_trees(idb, image, self.root / "bad-fit")
        blob = bytearray(idb.read_bytes())
        blob[-1] ^= 1
        idb.write_bytes(blob)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "image 1 sha256 mismatch"):
            extract_vendor_trees(idb, image, self.root / "bad-idb")

    def test_stale_spl_in_final_idb_is_rejected(self):
        idb, _ = self.vendor_inputs()
        artifacts = self.root / "packed"
        artifacts.mkdir()
        (artifacts / "idblock.img").write_bytes(idb.read_bytes())
        (artifacts / "u-boot-spl.bin").write_bytes(b"SPL-CODE" + self.spl.read_bytes())
        (artifacts / "u-boot-spl.dtb").write_bytes(self.spl.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(verify_packed_spl(artifacts)["control_dtb_verified"])
        # A stale boot payload must fail even when the exported DTB is correct.
        (artifacts / "u-boot-spl.bin").write_bytes(b"NEW-CODE" + self.spl.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "different SPL"):
            verify_packed_spl(artifacts)


if __name__ == "__main__":
    unittest.main()
