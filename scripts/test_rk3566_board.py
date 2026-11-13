#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""RK3566 early storage, firmware preservation and platform package isolation."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_rk3562_board as boards
import ci_rk3562 as ci
import package_rk3562_ports as package
import test_rk3562_control_dtb as fixtures
from rk3562_control_dtb import read_tree
from rk3562_mmc import (boot_mmc, extract_vendor_trees, validate_boot_storage,
                        verify_mmc_configuration, write_mmc_include)
from rk3562_release import release_identity


class RK3566Tests(unittest.TestCase):
    compile = fixtures.BootMMCTests.compile
    vendor_inputs = fixtures.BootMMCTests.vendor_inputs

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controllers = ('/sdhci@fe310000', '/dwmmc@fe2b0000')

    def fixture(self, name, modern=False):
        result = fixtures.BootMMCTests.fixture(self, name, modern=modern)
        source = result.with_suffix('.dts')
        source.write_text(source.read_text().replace('/mmc@ff870000', self.controllers[0])
                          .replace('/mmc@ff880000', self.controllers[1])
                          .replace('    mmc@ff870000', '    sdhci@fe310000')
                          .replace('    mmc@ff880000', '    dwmmc@fe2b0000')
                          .replace('sdmmc0-pins', 'sdmmc0_pins'))
        return self.compile(source, result)

    def test_matrix_keeps_soc_with_each_board(self):
        rows = ci.matrix('all', 'all')['include']
        self.assertIn(dict(board='powkiddy-x35h', soc='rk3566'), rows)
        self.assertIn(dict(board='ngp45h', soc='rk3562'), rows)
        with self.assertRaises(FileNotFoundError):
            ci.profile_for('ngp45h', 'rk3566')
        with self.assertRaises(FileNotFoundError):
            ci.profile_for('powkiddy-x35h')
        identity = release_identity(ref='rk356x-v1.1.0-2026-11-13', ref_type='tag', soc='rk356x')
        self.assertEqual(identity['release_tag'], 'rk356x-v1.1.0-2026-11-13')
        with self.assertRaises(ValueError):
            release_identity(ref='rk356x-v1.1.0-2026-11-13', ref_type='tag', soc='rk3566')

    def test_x35h_uses_its_own_config_and_embedded_dtb(self):
        profile = ci.profile_for('powkiddy-x35h', 'rk3566')
        out = self.root / 'defconfig'
        boards.render_config(profile, 'embedded', out)
        config = out.read_text()
        for expected in ('CONFIG_ROCKCHIP_RK3568=y',
                         'CONFIG_DEFAULT_DEVICE_TREE="rk3566-powkiddy-x35h"',
                         'CONFIG_LOADER_INI="RK3566MINIALL.ini"',
                         'CONFIG_EMBED_KERNEL_DTB_PATH="dts/rk3566-charge.dtb"',
                         '# CONFIG_DM_VIDEO is not set'):
            self.assertIn(expected + '\n', config)
        self.assertEqual(profile['control_dtb']['mmc_reference_sha256']['spl'],
                         profile['control_dtb']['mmc_reference_sha256']['uboot'])

    def test_rk3566_mmc_paths_and_tf_priority_are_verified(self):
        self.uboot, self.spl = self.fixture('uboot'), self.fixture('spl')
        self.assertEqual(boot_mmc(read_tree(self.uboot)), self.controllers)
        idb, fit = self.vendor_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            refs = extract_vendor_trees(idb, fit, self.root / 'vendor')
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        for key, name in [('uboot', 'u-boot.dtb'), ('spl', 'u-boot-spl.dtb')]:
            shutil.copy2(refs[key], artifacts / name)
        report = validate_boot_storage(refs, artifacts)
        self.assertTrue(report['stages']['spl']['tf1_before_emmc'])
        subprocess.run(['fdtput', '-t', 's', str(artifacts / 'u-boot.dtb'), '/chosen',
                        'u-boot,spl-boot-order', *self.controllers], check=True)
        with self.assertRaisesRegex(ValueError, 'prefer TF1'):
            validate_boot_storage(refs, artifacts)

    def test_rk3566_pin_configuration_and_linux_modes_are_not_shared(self):
        base, vendor = self.fixture('linux', modern=True), self.fixture('vendor')
        include = self.root / 'mmc.dtsi'
        write_mmc_include(base, vendor, include)
        source = self.root / 'control.dts'
        source.write_text(base.with_suffix('.dts').read_text() + '\n/include/ "mmc.dtsi"\n')
        output = self.compile(source, self.root / 'control.dtb')
        verify_mmc_configuration(vendor, output, self.controllers)
        subprocess.run(['fdtput', '-t', 'i', str(output), self.controllers[1],
                        'max-frequency', '200000000'], check=True)
        with self.assertRaisesRegex(ValueError, 'MMC configuration changed'):
            verify_mmc_configuration(vendor, output, self.controllers)

    def test_atf_renumbering_preserves_addresses_and_bytes(self):
        old = [dict(name='atf-1', load='0x40000', size=32, sha256='a' * 64),
               dict(name='atf-2', load='0xfdcd0000', size=32, sha256='b' * 64)]
        renamed = [dict(old[1], name='atf-1'), dict(old[0], name='atf-2')]
        self.assertEqual(boards.firmware_payloads(old), boards.firmware_payloads(renamed))
        for key, value in [('load', '0x50000'), ('size', 64), ('sha256', 'c' * 64)]:
            bad = [dict(renamed[0], **{key: value}), renamed[1]]
            self.assertNotEqual(boards.firmware_payloads(old), boards.firmware_payloads(bad))

    def test_ports_bundle_is_limited_to_rk3566(self):
        build = self.root / 'build'
        build.mkdir()
        image = build / 'rk3566-uboot-16m.bin'
        with image.open('wb') as stream:
            stream.truncate(16744448)
        (build / 'board-mmc.json').write_text(json.dumps({
            'packed_spl': {'control_dtb_verified': True},
            'stages': {s: {'tf1_before_emmc': True} for s in ('spl', 'uboot')}}))
        (build / 'board-power.json').write_text('{}')
        metadata = dict(soc='rk3566', board_id='powkiddy-x35h', dtb_only=False,
                        dtb_mode='embedded', source_commit='a' * 40,
                        tracked_source_diff_sha256=hashlib.sha256(b'').hexdigest(),
                        artifacts={p.name: package.sha256(p) for p in build.iterdir()})
        (build / 'board-build.json').write_text(json.dumps(metadata))
        def screens(output, font):
            output.mkdir()
        output = self.root / 'ports'
        with patch.object(package, 'screens', side_effect=screens):
            package.build_ports([build], output, self.root, require_all=True,
                                version='1.1.0', release_date='2026-11-13', soc='rk3566')
            with self.assertRaisesRegex(ValueError, 'different SoC'):
                package.build_ports([build], self.root / 'wrong', self.root)
        archive = package.archive_ports(output, self.root / 'dist')
        self.assertEqual(archive.name, 'rk3566-update-uboot-ports-2026-11-13-v1.1.0.zip')
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read('uboot/devices.json'))
            self.assertEqual([d['id'] for d in manifest['devices']], ['powkiddy-x35h'])
            self.assertNotIn('uboot/ngp45h-uboot.bin', bundle.namelist())


if __name__ == '__main__':
    unittest.main()
