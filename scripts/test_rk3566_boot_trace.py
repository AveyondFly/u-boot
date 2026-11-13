#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Persist and recover boot stages without needing a successful Linux boot."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from read_rk3566_boot_trace import extract_boot_trace, VENDOR_OFFSET, SLOT_SIZE, TRACE_ID

ROOT = Path(__file__).resolve().parents[1]


class TraceTests(unittest.TestCase):
    def fixture(self, version=1, trace=True):
        slot = bytearray(SLOT_SIZE)
        payload = b'RK3566-BOOT-TRACE v1\nnext-dev-test\nprobe-start:0\n'
        struct.pack_into('<IIHH', slot, 0, 0x524b5644, version, 1, 1 if trace else 0)
        struct.pack_into('<I', slot, SLOT_SIZE - 4, version)
        if trace:
            struct.pack_into('<4H', slot, 16, TRACE_ID, 0, len(payload), 0)
            slot[1024:1024 + len(payload)] = payload
        return slot

    def test_latest_record_is_used_and_stale_trace_is_rejected(self):
        blob = bytearray(VENDOR_OFFSET + 4 * SLOT_SIZE)
        blob[VENDOR_OFFSET:VENDOR_OFFSET + SLOT_SIZE] = self.fixture()
        self.assertIn('probe-start:0', extract_boot_trace(blob))
        blob[VENDOR_OFFSET + SLOT_SIZE:VENDOR_OFFSET + 2 * SLOT_SIZE] = self.fixture(2, False)
        with self.assertRaisesRegex(ValueError, 'latest vendor-storage record'):
            extract_boot_trace(blob)
        blob[VENDOR_OFFSET + 2 * SLOT_SIZE:VENDOR_OFFSET + 3 * SLOT_SIZE] = self.fixture(3)
        self.assertIn('probe-start:0', extract_boot_trace(blob))

    def test_torn_or_out_of_bounds_records_are_rejected(self):
        blob = bytearray(VENDOR_OFFSET + SLOT_SIZE)
        blob[VENDOR_OFFSET:] = self.fixture()
        struct.pack_into('<I', blob, VENDOR_OFFSET + SLOT_SIZE - 4, 99)
        with self.assertRaisesRegex(ValueError, 'No valid vendor-storage'):
            extract_boot_trace(blob)
        blob[VENDOR_OFFSET:] = self.fixture()
        struct.pack_into('<H', blob, VENDOR_OFFSET + 18, SLOT_SIZE - 1)
        with self.assertRaisesRegex(ValueError, 'outside vendor-storage'):
            extract_boot_trace(blob)

    def test_actual_c_writer_persists_bounded_trace_and_starts_fresh(self):
        source = (ROOT / 'arch/arm/mach-rockchip/boot_trace.c').read_text()
        def actual_function(name):
            start = source.index('void ' + name + '(')
            end = source.index('{', start) + 1
            depth = 1
            while depth:
                depth += (source[end] == '{') - (source[end] == '}')
                end += 1
            return source[start:end] + '\n'
        functions = actual_function('rockchip_boot_trace_start') + actual_function('rockchip_boot_trace')
        pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define U_BOOT_VERSION "next-dev-test"
#define ROCKCHIP_BOOT_TRACE_ID 0xff00
static char boot_trace[2048], saved[2048];
static bool trace_ready;
static unsigned writes;
static int vendor_storage_write(unsigned id, void *data, unsigned size) {
 assert(id==0xff00 && size<=sizeof(saved));
 memcpy(saved,data,size); writes++; return 0;
}
void rockchip_boot_trace(const char *stage, int value);
'''
        post = r'''
int main(void) {
 rockchip_boot_trace("not-ready",0); assert(writes==0);
 rockchip_boot_trace_start(); assert(strstr(saved,"late-init:0\n"));
 rockchip_boot_trace("probe-start",0);
 rockchip_boot_trace("booti-images",-22);
 assert(strstr(saved,"probe-start:0\n") && strstr(saved,"booti-images:-22\n"));
 for(unsigned i=0;i<10000;i++) rockchip_boot_trace("bounded",i);
 assert(strlen(saved)<sizeof(saved) && writes<500);
 rockchip_boot_trace_start(); assert(!strstr(saved,"probe-start"));
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            src, exe = Path(temporary) / 'test.c', Path(temporary) / 'test'
            src.write_text(pre + functions + post)
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(src), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
