/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __ROCKCHIP_BOOT_TRACE_H
#define __ROCKCHIP_BOOT_TRACE_H

#define ROCKCHIP_BOOT_TRACE_ID 0xff00

#ifdef CONFIG_ROCKCHIP_BOOT_TRACE
void rockchip_boot_trace_start(void);
void rockchip_boot_trace(const char *stage, int value);
#else
static inline void rockchip_boot_trace_start(void) {}
static inline void rockchip_boot_trace(const char *stage, int value) {}
#endif

#endif
