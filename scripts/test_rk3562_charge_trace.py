#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
s=(ROOT / 'drivers/power/charge-display-uclass.c').read_text()
functions=s[s.index('#define CHARGE_TRACE_SIZE'):s.index('\nint charge_display_show')]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
static char saved_trace[512], bootargs[2048];
static bool has_trace;
static const char *env_get(const char *name) {
 assert(!strcmp(name,"charge_trace"));
 return has_trace ? saved_trace : NULL;
}
static int env_set(const char *name, const char *value) {
 assert(!strcmp(name,"charge_trace"));
 has_trace=value!=NULL;
 if(value) { assert(strlen(value)<sizeof(saved_trace)); strcpy(saved_trace,value); }
 return 0;
}
static int env_update(const char *name, const char *argument) {
 assert(!strcmp(name,"bootargs"));
 assert(strlen(bootargs)+strlen(argument)+2<sizeof(bootargs));
 strcat(bootargs," "); strcat(bootargs,argument); return 0;
}
'''
post=r'''
int main(void) {
 strcpy(bootargs,"root=test");
 charge_display_add_bootargs();
 assert(!strcmp(bootargs,"root=test"));
 charge_display_trace("enabled",1);
 charge_display_trace("pmic",0);
 charge_display_trace("gauge",-19);
 assert(!strcmp(saved_trace,"v4-pmic,enabled:1,pmic:0,gauge:-19"));
 /* extlinux replaces bootargs before the final Rockchip bootargs hook. */
 strcpy(bootargs,"extlinux=1");
 charge_display_add_bootargs();
 assert(!strcmp(bootargs,"extlinux=1 uboot.charge=v4-pmic,enabled:1,pmic:0,gauge:-19"));
 for(int i=0;i<1000;i++) charge_display_trace("overflow-check",i);
 assert(strlen(saved_trace)<CHARGE_TRACE_SIZE);
 char previous[512]; strcpy(previous,saved_trace);
 charge_display_trace("this-stage-cannot-fit-in-the-existing-trace",123456789);
 assert(!strcmp(previous,saved_trace));
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="rk3562-charge-test-") as tmp:
    p = Path(tmp) / "test.c"
    binary = Path(tmp) / "test"
    p.write_text(pre+functions+post)
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare", str(p), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: stage/errno capture, no-trace handling, extlinux replacement, bounded trace')
