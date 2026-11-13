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


def actual_function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


class TraceTests(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            src, exe = Path(temporary) / 'test.c', Path(temporary) / 'test'
            src.write_text(source)
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(src), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

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
        functions = actual_function(source, 'void rockchip_boot_trace_start(') + actual_function(source, 'void rockchip_boot_trace(')
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
        self.compile_run(pre + functions + post)

    def test_console_errors_are_saved_without_recursive_recording(self):
        source = (ROOT / 'arch/arm/mach-rockchip/boot_trace.c').read_text()
        functions = actual_function(source, 'void rockchip_boot_trace_console(')
        self.compile_run(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define ROCKCHIP_BOOT_TRACE_ID 0xff00
#define GD_FLG_RECORD 0x1000
struct membuff { char *start; unsigned position; };
struct global_data { unsigned long flags; struct membuff console_out; } data, *gd=&data;
static bool trace_ready;
static char boot_trace[12288], saved[12288];
static unsigned writes;
static int membuff_getbyte(struct membuff *mb) {
 assert(!(gd->flags & GD_FLG_RECORD));
 return mb->start[mb->position] ? (unsigned char)mb->start[mb->position++] : -1;
}
static int vendor_storage_write(unsigned id, void *data, unsigned size) {
 assert(!(gd->flags & GD_FLG_RECORD));
 assert(id==0xff00 && size<=sizeof(saved));
 memcpy(saved,data,size); writes++; return 0;
}
''' + functions + r'''
int main(void) {
 rockchip_boot_trace_console(); assert(writes==0);
 trace_ready=true;
 rockchip_boot_trace_console(); assert(writes==0);
 char output[8192]; memset(output,'x',sizeof(output)-1); output[8191]=0;
 strcpy(output+8100,"Error reading config file\n");
 strcpy(boot_trace,"RK3566-BOOT-TRACE v1\nboot-command-return:1\n");
 gd->console_out.start=output; gd->flags=GD_FLG_RECORD|2;
 rockchip_boot_trace_console();
 assert(writes==1 && gd->flags==(GD_FLG_RECORD|2));
 assert(strstr(saved,"boot-command-return:1\nconsole-output:\n"));
 assert(strstr(saved,"Error reading config file\n"));
 memset(boot_trace,'x',sizeof(boot_trace)-1); boot_trace[sizeof(boot_trace)-1]=0;
 rockchip_boot_trace_console(); assert(writes==1);
 assert(gd->flags==(GD_FLG_RECORD|2));
 return 0;
}
''')

    def test_autoboot_records_execution_result_separately_from_skipped_boot(self):
        source = (ROOT / 'common/autoboot.c').read_text()
        function = actual_function(source, 'void autoboot_command(')
        self.compile_run(r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_ROCKCHIP_BOOT_TRACE 1
#define debug(...) ((void)0)
static int stored_bootdelay, aborted, command_calls, command_result, fail_calls;
static char stages[1024];
static int abortboot(int delay) { assert(delay==stored_bootdelay); return aborted; }
static void rockchip_boot_trace(const char *stage,int value) {
 char line[100]; snprintf(line,sizeof(line),"%s:%d\n",stage,value); strcat(stages,line);
}
static int run_command_list(const char *s,int length,int flag) {
 assert(!strcmp(s,"run distro_bootcmd") && length==-1 && flag==0);
 command_calls++; return command_result;
}
static void autoboot_command_fail_handle(void) { fail_calls++; }
''' + function + r'''
int main(void) {
 stored_bootdelay=-1; autoboot_command("run distro_bootcmd");
 assert(command_calls==0 && !stages[0]);
 stored_bootdelay=0; aborted=1; autoboot_command("run distro_bootcmd");
 assert(command_calls==0 && !stages[0]);
 aborted=0; command_result=1; autoboot_command("run distro_bootcmd");
 assert(command_calls==1 && fail_calls==1);
 assert(strstr(stages,"boot-command-start:0\nboot-command-return:1\n"));
 return 0;
}
''')


if __name__ == '__main__':
    unittest.main()
