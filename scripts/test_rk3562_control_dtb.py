#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Regression checks for board DTB export and power configuration isolation."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import build_rk3562_board as boards
from rk3562_control_dtb import power_configuration, read_tree, vendor_dtsi, verify_power_configuration


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


if __name__ == "__main__":
    unittest.main()
