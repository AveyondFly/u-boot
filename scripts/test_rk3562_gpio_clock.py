#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Host regression for gated RK3562 GPIO reads and CRU hiword writes."""

from pathlib import Path
import subprocess
import tempfile

from test_rk817_dc import function

ROOT = Path(__file__).resolve().parents[1]


def main():
    clock = function((ROOT / "drivers/clk/rockchip/clk_rk3562.c").read_text(),
                     "rk3562_clk_enable")
    gpio = function((ROOT / "drivers/gpio/rk_gpio.c").read_text(),
                    "rockchip_gpio_get_value")
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <string.h>
typedef uint32_t u32;
typedef unsigned long ulong;
#define BIT(n) (1U << (n))
#define OFFSET_TO_BIT(n) BIT(n)
#define check_member(type, member, offset) \
 _Static_assert(offsetof(struct type, member) == (offset), "register offset")
#define CONFIG_ROCKCHIP_GPIO_V2 1
#include "arch/arm/include/asm/arch-rockchip/cru_rk3562.h"
#include "arch/arm/include/asm/arch-rockchip/gpio.h"
#include "include/dt-bindings/clock/rk3562-cru.h"
static struct rk3562_cru cru;
static struct rockchip_gpio_regs gpio_regs;
struct udevice { void *priv; };
struct clk { struct udevice *dev; ulong id; };
struct rockchip_gpio_priv { struct rockchip_gpio_regs *regs; };
static void *dev_get_priv(struct udevice *dev) { return dev->priv; }
static unsigned writes, traces;
static void charge_display_trace(const char *name, int value) {
 (void)name; (void)value; traces++;
}
static void writel(u32 value, volatile u32 *address) {
 /* Model CRU upper-half write enables, preserving all other gates. */
 u32 mask=value >> 16;
 assert(mask);
 *address=(*address & ~mask) | (value & mask);
 writes++;
}
#include "arch/arm/include/asm/arch-rockchip/hardware.h"
static u32 readl(const volatile u32 *address) {
 if(address==&gpio_regs.ext_port &&
    ((cru.gate_con[18] & BIT(2)) ||
     (cru.gate_con[25] & (BIT(5)|BIT(6))) ||
     (cru.gate_con[26] & (BIT(0)|BIT(2))))) return 0;
 return *address;
}
'''
    tests = r'''
int main(void) {
 _Static_assert(offsetof(struct rk3562_cru, gate_con[26])==0x368, "GPIO3 APB gate");
 _Static_assert(offsetof(struct rockchip_gpio_regs, ext_port)==0x70, "GPIO V2 input");
 struct rk3562_clk_priv priv={.cru=&cru};
 struct udevice clock_dev={&priv};
 struct clk clk={&clock_dev,PCLK_GPIO3_VCCIO156};
 struct rockchip_gpio_priv gp={&gpio_regs};
 struct udevice gpio_dev={&gp};
 memset(&cru,0xff,sizeof(cru));
 gpio_regs.ext_port=BIT(7);
 /* GPIO3_A7 is physically high but reads low while its bus is gated. */
 assert(rockchip_gpio_get_value(&gpio_dev,7)==0);
 assert(rk3562_clk_enable(&clk)==0);
 assert(rockchip_gpio_get_value(&gpio_dev,7)==1);
 gpio_regs.ext_port=0;
 assert(rockchip_gpio_get_value(&gpio_dev,7)==0);
 assert(cru.gate_con[18]==(0xffffffffU & ~BIT(2)));
 assert(cru.gate_con[25]==(0xffffffffU & ~(BIT(5)|BIT(6))));
 assert(cru.gate_con[26]==(0xffffffffU & ~(BIT(0)|BIT(1)|BIT(2))));
 assert(cru.gate_con[20]==0xffffffffU); /* debounce clock unchanged */
 assert(writes==3 && traces==3);
 /* Idempotent enables and other banks retain unrelated gate bits. */
 assert(rk3562_clk_enable(&clk)==0);
 clk.id=PCLK_GPIO4_VCCIO156;
 assert(rk3562_clk_enable(&clk)==0);
 assert(cru.gate_con[26]==(0xffffffffU & ~15U));
 clk.id=PCLK_PERI_GPIO1;
 assert(rk3562_clk_enable(&clk)==0);
 assert(cru.perigate_con[17]==0xfffffffeU);
 clk.id=PCLK_PERI_GPIO2;
 assert(rk3562_clk_enable(&clk)==0);
 assert(cru.perigate_con[17]==0xfffffffcU);
 assert(cru.perigate_con[1]==(0xffffffffU & ~BIT(2)));
 assert(cru.perigate_con[16]==(0xffffffffU & ~BIT(12)));
 clk.id=PCLK_PMU0_GPIO0;
 assert(rk3562_clk_enable(&clk)==0);
 assert(cru.pmu0gate_con[0]==(0xffffffffU & ~(BIT(8)|BIT(9)|BIT(14))));
 unsigned previous=writes;
 clk.id=0xdead;
 assert(rk3562_clk_enable(&clk)==-ENOSYS && writes==previous);
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="rk3562-gpio-clock-test-") as tmp:
        source = Path(tmp) / "test.c"
        source.write_text(harness + clock + gpio + tests)
        binary = Path(tmp) / "test"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT), str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("PASS: gated DC input, GPIO0..4 gates/parents, MMIO layout, hiword masks, unplug and unsupported clocks")


if __name__ == "__main__":
    main()
