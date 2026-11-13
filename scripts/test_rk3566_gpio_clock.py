#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Execute RK3566 GPIO clock enables with masked MMIO writes on the host."""
from pathlib import Path
import subprocess
import tempfile
from test_rk817_dc import function

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'drivers/clk/rockchip/clk_rk3568.c').read_text()
functions = function(source, 'rk3568_clk_enable') + function(source, 'rk3568_pmuclk_enable')
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint32_t u32;
typedef unsigned long ulong;
#define BIT(n) (1U << (n))
#define check_member(type, member, offset) \
 _Static_assert(offsetof(struct type, member) == (offset), "register offset")
#include "arch/arm/include/asm/arch-rockchip/cru_rk3568.h"
#include "include/dt-bindings/clock/rk3568-cru.h"
struct udevice { void *priv; };
struct clk { struct udevice *dev; ulong id; };
static void *dev_get_priv(struct udevice *dev) { return dev->priv; }
static unsigned writes;
static void writel(u32 value, volatile u32 *address) {
 u32 mask = value >> 16;
 assert(mask);
 *address = (*address & ~mask) | (value & mask);
 writes++;
}
#include "arch/arm/include/asm/arch-rockchip/hardware.h"
'''
post = r'''
int main(void) {
 struct rk3568_cru cru;
 struct rk3568_pmucru pmu;
 struct rk3568_clk_priv priv = {.cru=&cru};
 struct rk3568_pmuclk_priv ppriv = {.pmucru=&pmu};
 struct udevice dev = {&priv}, pdev = {&ppriv};
 struct clk clk = {&dev, PCLK_GPIO4}, pclk = {&pdev, PCLK_GPIO0};
 memset(&cru, 0xff, sizeof(cru));
 memset(&pmu, 0xff, sizeof(pmu));
 assert(rk3568_clk_enable(&clk) == 0);
 assert(cru.clkgate_con[26] == (0xffffffffU & ~BIT(1)));
 assert(cru.clkgate_con[31] == (0xffffffffU & ~BIT(8)));
 assert(cru.clkgate_con[32] == 0xffffffffU);
 for(unsigned id=PCLK_GPIO1; id<=PCLK_GPIO4; id+=2) {
  clk.id=id; assert(rk3568_clk_enable(&clk) == 0);
 }
 assert(cru.clkgate_con[31] == (0xffffffffU & ~(BIT(2)|BIT(4)|BIT(6)|BIT(8))));
 assert(rk3568_pmuclk_enable(&pclk) == 0);
 assert(pmu.pmu_clkgate_con[0] == (0xffffffffU & ~BIT(2)));
 assert(pmu.pmu_clkgate_con[1] == (0xffffffffU & ~BIT(9)));
 unsigned before=writes;
 clk.id=0xdead; pclk.id=0xdead;
 assert(rk3568_clk_enable(&clk) == 0);
 assert(rk3568_pmuclk_enable(&pclk) == 0);
 assert(writes == before);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='rk3566-gpio-test-') as tmp:
    src, binary = Path(tmp) / 'test.c', Path(tmp) / 'test'
    src.write_text(pre + functions + post)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT),
                    str(src), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: RK3566 GPIO0..4 clocks, parent gates, MMIO offsets and unrelated-bit preservation')
