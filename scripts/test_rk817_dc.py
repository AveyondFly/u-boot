#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Exercise the actual RK817 DC detection functions with host GPIO/PMIC stubs."""

from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = source.rfind("\nstatic ", 0, source.index(name + "(")) + 1
    brace = source.index("{", source.index(name + "("))
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def main():
    source = (ROOT / "drivers/power/fuel_gauge/fg_rk817.c").read_text()
    names = ["rk817_bat_dc_is_online", "rk817_bat_power_is_online",
             "rk817_bat_update_get_chrg_online", "rk817_bat_get_usb_state",
             "rk817_bat_get_charger_type", "rk817_fg_init_dc_det",
             "rk817_fg_init_usb_input"]
    functions = "\n".join(function(source, name) for name in names)
    stubs = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#include <string.h>
typedef unsigned u32;
typedef unsigned char u8;
typedef unsigned long ulong;
#define PLUG_IN_STS (1 << 6)
#define PMIC_SYS_STS 0xf0
#define PMIC_CHRG_STS 0xeb
#define RK817_ON_SOURCE 0xf5
#define CHARGE_FINISH 0x40
#define RK817_ID 0x8170
#define RK817_POWER_EN3 0xb4
#define PMIC_CHRG_OUT 0xe4
#define PMIC_CHRG_TERM 0xe6
#define RK817_USB_INPUT_EN (1 << 6)
#define RK817_POWERPATH_EN (1 << 7)
#define RK817_OTG_EN (1 << 2)
#define RK817_OTG_EN_MASK (1 << 6)
#define GPIOD_IS_IN 1
#define dev_err(...) ((void)0)
enum { NO_CHARGER, USB_CHARGER, AC_CHARGER, DC_CHARGER, UNDEF_CHARGER };
enum { POWER_SUPPLY_TYPE_UNKNOWN, POWER_SUPPLY_TYPE_USB, POWER_SUPPLY_TYPE_USB_DCP,
       POWER_SUPPLY_TYPE_USB_CDP, POWER_SUPPLY_TYPE_USB_FLOATING };
struct udevice;
static unsigned dev_read_addr(struct udevice *dev) { (void)dev; return 0xffac0000; }
struct gpio_dev_priv { unsigned gpio_base; };
static struct gpio_dev_priv gpio_bank = {96};
static void *dev_get_uclass_priv(struct udevice *dev) { (void)dev; return &gpio_bank; }
struct gpio_desc { bool valid, active_low; int raw, error; unsigned offset, flags; struct udevice *dev; };
struct dm_gpio_ops { int (*get_function)(struct udevice *, unsigned); };
static int gpio_function(struct udevice *dev, unsigned offset) { (void)dev; (void)offset; return 1; }
static const struct dm_gpio_ops gpio_ops = {gpio_function};
#define gpio_get_ops(dev) (&gpio_ops)
static int trace_count;
static void charge_display_trace(const char *stage, int value) { (void)stage; (void)value; trace_count++; }
struct node { bool has_dc; };
typedef struct node *ofnode;
struct udevice { void *priv; struct udevice *parent; ofnode node, charger; const char *source; };
struct rk8xx_priv { int variant; };
struct rk817_battery_device { struct udevice *dev; struct gpio_desc dc_det; bool power_trace_done, usb_input_init; int virtual_power, rsoc, variant, usb_input_sys_before, voltage_usb; u32 charger_detect_timeout_ms; };
static int sys_status, charge_status, usb_type, request_error, requests;
static ulong now, input_appears;
static ulong get_timer(ulong base) { return now-base; }
static void mdelay(unsigned ms) {
 now+=ms;
 if(input_appears && now>=input_appears) sys_status=PLUG_IN_STS;
}
static int regs[256], read_error_reg=-1, fail_write, write_count;
static int pmic_reg_read(struct udevice *dev, unsigned reg) {
 (void)dev; return (int)reg==read_error_reg ? -EIO : regs[reg];
}
static int pmic_reg_write(struct udevice *dev, unsigned reg, unsigned value) {
 (void)dev;
 if(++write_count==fail_write) return -EIO;
 if(reg==RK817_POWER_EN3) {
  unsigned mask=(value>>4)&15;
  regs[reg]=(regs[reg]&~mask)|(value&mask);
 } else regs[reg]=value;
 return 0;
}
static ofnode requested_node;
static void *dev_get_priv(struct udevice *d) { return d->priv; }
static bool dm_gpio_is_valid(const struct gpio_desc *d) { return d->valid; }
static int dm_gpio_get_value(const struct gpio_desc *d) {
 if(d->error) return d->error;
 return d->active_low ? !d->raw : d->raw;
}
static int rk817_bat_read(struct rk817_battery_device *b, int reg) {
 (void)b; return reg==PMIC_SYS_STS ? sys_status : charge_status;
}
static int rk817_bat_dwc_otg_check_dpdm(struct rk817_battery_device *b) {
 (void)b; return usb_type;
}
static ofnode dev_ofnode(struct udevice *d) { return d->node; }
static const char *dev_read_string(struct udevice *d, const char *name) {
 assert(!strcmp(name,"rockchip,charger-detect-source")); return d->source;
}
static bool ofnode_valid(ofnode n) { return n!=NULL; }
static const void *ofnode_get_property(ofnode n, const char *name, void *len) {
 (void)len; assert(!strcmp(name,"dc_det_gpio")); return n && n->has_dc ? n : NULL;
}
static ofnode dev_read_subnode(struct udevice *d, const char *name) {
 assert(!strcmp(name,"charger")); return d->charger;
}
static int gpio_request_by_name_nodev(ofnode n, const char *name, int index,
                                    struct gpio_desc *d, int flags) {
 assert(!strcmp(name,"dc_det_gpio") && index==0 && flags==GPIOD_IS_IN);
 requests++; requested_node=n;
 if(request_error) return request_error;
 d->valid=true; return 0;
}
'''
    tests = r'''
int main(void) {
 struct rk8xx_priv pmic={RK817_ID};
 struct node battery_node={true}, charger_node={true}, empty={false};
 struct udevice parent={.priv=&pmic,.charger=&charger_node};
 struct rk817_battery_device b={0};
 struct udevice dev={.priv=&b,.parent=&parent,.node=&battery_node};
 b.dev=&dev;
 assert(rk817_fg_init_dc_det(&dev)==0 && requested_node==&battery_node);
 dev.node=&empty;
 assert(rk817_fg_init_dc_det(&dev)==0 && requested_node==&charger_node);
 parent.charger=NULL;
 int count=requests;
 assert(rk817_fg_init_dc_det(&dev)==0 && requests==count);
 dev.node=&battery_node; request_error=-EBUSY;
 assert(rk817_fg_init_dc_det(&dev)==-EBUSY);
 request_error=0;
 /* Ignore the inherited, stuck-high GPIO when the board selects PMIC. */
 count=requests; dev.source="pmic"; b.dc_det.valid=false;
 assert(rk817_fg_init_dc_det(&dev)==0 && requests==count && !b.dc_det.valid);
 dev.source="invalid";
 assert(rk817_fg_init_dc_det(&dev)==-EINVAL && requests==count);
 dev.source="auto";
 assert(rk817_fg_init_dc_det(&dev)==0 && requests==count+1);
 dev.source=NULL;
 /* The reported failure: DC present, PMIC USB bit clear. */
 b.dc_det=(struct gpio_desc){.valid=true,.raw=1};
 sys_status=0; charge_status=0; usb_type=POWER_SUPPLY_TYPE_UNKNOWN;
 assert(rk817_bat_update_get_chrg_online(&dev));
 int initial_trace_count=trace_count;
 assert(initial_trace_count>0);
 assert(rk817_bat_update_get_chrg_online(&dev));
 assert(trace_count==initial_trace_count);
 assert(rk817_bat_get_usb_state(&b)==DC_CHARGER);
 assert(rk817_bat_get_charger_type(&b)==DC_CHARGER);
 /* Unplugging DC must allow the charging loop to shut down. */
 b.dc_det.raw=0;
 assert(!rk817_bat_update_get_chrg_online(&dev));
 assert(rk817_bat_get_charger_type(&b)==NO_CHARGER);
 /* Active-low boards get the same logical detection. */
 b.dc_det.active_low=true;
 assert(rk817_bat_update_get_chrg_online(&dev));
 b.dc_det.raw=1;
 assert(!rk817_bat_update_get_chrg_online(&dev));
 /* Existing USB-only boards keep working, including charger type. */
 b.dc_det.valid=false; sys_status=PLUG_IN_STS;
 usb_type=POWER_SUPPLY_TYPE_USB;
 assert(rk817_bat_update_get_chrg_online(&dev));
 assert(rk817_bat_get_charger_type(&b)==USB_CHARGER);
 usb_type=POWER_SUPPLY_TYPE_USB_DCP;
 assert(rk817_bat_get_charger_type(&b)==AC_CHARGER);
 /* No input / negative GPIO and I2C errors must not invent a charger. */
 sys_status=0; assert(!rk817_bat_update_get_chrg_online(&dev));
 b.dc_det.valid=true; b.dc_det.error=-EIO; sys_status=-EIO;
 assert(!rk817_bat_update_get_chrg_online(&dev));
 assert(rk817_bat_get_charger_type(&b)==NO_CHARGER);
 b.dc_det.error=0; b.dc_det.active_low=false; b.dc_det.raw=1; sys_status=0;
 b.rsoc=100000;
 assert(rk817_bat_get_charger_type(&b)==CHARGE_FINISH);
 /* Sink setup preserves voltage/current limits, boost and termination bits. */
 assert(rk817_fg_init_usb_input(&b)==0 && write_count==0);
 b.usb_input_init=true; sys_status=0x82;
 regs[RK817_POWER_EN3]=6; regs[PMIC_CHRG_OUT]=0x5b; regs[PMIC_CHRG_TERM]=2;
 assert(rk817_fg_init_usb_input(&b)==0);
 assert(b.usb_input_sys_before==0x82);
 assert(regs[RK817_POWER_EN3]==2); /* OTG off, boost unchanged */
 assert(regs[PMIC_CHRG_OUT]==0xdb && regs[PMIC_CHRG_TERM]==0x42);
 /* I2C failures must stop initialization instead of claiming success. */
 sys_status=-EIO; write_count=0;
 assert(rk817_fg_init_usb_input(&b)==-EIO && write_count==0);
 sys_status=0;
 const int read_regs[]={RK817_POWER_EN3,PMIC_CHRG_OUT,PMIC_CHRG_TERM};
 for(int i=0;i<3;i++) {
  read_error_reg=read_regs[i]; write_count=0;
  assert(rk817_fg_init_usb_input(&b)==-EIO && write_count==i);
 }
 read_error_reg=-1;
 for(int i=1;i<=3;i++) {
  fail_write=i; write_count=0;
  assert(rk817_fg_init_usb_input(&b)==-EIO && write_count==i);
 }
 fail_write=0;
 /* Allow bounded comparator settling, then continue to use live status. */
 b.dc_det.valid=false; b.power_trace_done=false;
 b.charger_detect_timeout_ms=300; sys_status=0; now=0; input_appears=100;
 assert(rk817_bat_update_get_chrg_online(&dev) && now==100);
 input_appears=0; sys_status=0;
 assert(!rk817_bat_update_get_chrg_online(&dev) && now==100);
 b.power_trace_done=false; now=0;
 assert(!rk817_bat_update_get_chrg_online(&dev) && now==300);
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="rk817-dc-test-") as tmp:
        path = Path(tmp) / "test.c"
        path.write_text(stubs + functions + tests)
        binary = Path(tmp) / "test"
        subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                        str(path), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("PASS: DC/PMIC selection, bounded detection, unplug, sink setup, preserved limits, I2C/GPIO failures and full charge")


if __name__ == "__main__":
    main()
