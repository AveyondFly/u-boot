#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Recovery is tested only against regular temporary files, never a disk."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
spec = importlib.util.spec_from_file_location('recovery', Path(__file__).resolve().parents[1] / 'tools/restore_x35h_tf.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reference = self.root / 'reference.bin'
        self.good = b'MBR' + bytes(recovery.OFFSET - 3) + b'G' * (recovery.FRONT_SIZE - recovery.OFFSET)
        self.reference.write_bytes(self.good)
        self.device = self.root / 'card.bin'
        self.failed = self.good[:recovery.OFFSET] + b'F' * (recovery.FRONT_SIZE - recovery.OFFSET)
        self.tail = b'partition-content-preserved'
        self.device.write_bytes(self.failed + self.tail)
        patcher = patch.object(recovery, 'REFERENCE_SHA256', recovery.digest(self.good))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_restore_backs_up_and_changes_only_the_boot_region(self):
        backup = self.root / 'backup'
        result = recovery.restore(self.device, self.reference, backup, True, regular_fixture=True)
        self.assertTrue(result['written'])
        self.assertEqual((backup / 'before-first16m.bin').read_bytes(), self.failed)
        self.assertEqual(self.device.read_bytes(), self.good + self.tail)

    def test_check_only_wrong_card_and_bad_reference_do_not_write(self):
        backup = self.root / 'backup'
        result = recovery.restore(self.device, self.reference, backup, regular_fixture=True)
        self.assertFalse(result['written'])
        self.assertFalse(backup.exists())
        self.assertEqual(self.device.read_bytes(), self.failed + self.tail)
        data = bytearray(self.device.read_bytes()); data[446] ^= 1; self.device.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'first 32 KiB differs'):
            recovery.restore(self.device, self.reference, backup, True, regular_fixture=True)
        self.reference.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'known-working'):
            recovery.restore(self.device, self.reference, backup, True, regular_fixture=True)
        self.assertEqual(self.device.read_bytes(), data)

    def test_cli_mode_rejects_regular_files_and_existing_backup_aborts_write(self):
        with self.assertRaisesRegex(ValueError, 'block device'):
            recovery.restore(self.device, self.reference, self.root / 'backup', True)
        backup = self.root / 'existing'; backup.mkdir()
        with self.assertRaises(FileExistsError):
            recovery.restore(self.device, self.reference, backup, True, regular_fixture=True)
        self.assertEqual(self.device.read_bytes(), self.failed + self.tail)

    def test_diagnostic_trace_is_saved_before_restoring(self):
        trace = 'RK3566-BOOT-TRACE v1\nnext-dev-test\nprobe-start:0\n'
        with patch.object(recovery, 'extract_boot_trace', return_value=trace):
            result = recovery.restore(self.device, self.reference, self.root / 'traced',
                                      True, regular_fixture=True)
        self.assertEqual(Path(result['boot_trace']).read_text(), trace)
        self.assertEqual(self.device.read_bytes(), self.good + self.tail)


if __name__ == '__main__':
    unittest.main()
