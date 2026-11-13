// SPDX-License-Identifier: GPL-2.0+
/* Deliberately enabled only for handheld boot diagnostics. */
#include <common.h>
#include <version.h>
#include <asm/arch/boot_trace.h>
#include <asm/arch/vendor.h>

static char boot_trace[2048];
static bool trace_ready;

void rockchip_boot_trace_start(void)
{
	snprintf(boot_trace, sizeof(boot_trace), "RK3566-BOOT-TRACE v1\n%s\n",
		 U_BOOT_VERSION);
	trace_ready = true;
	rockchip_boot_trace("late-init", 0);
}

void rockchip_boot_trace(const char *stage, int value)
{
	size_t length;
	int ret;

	if (!trace_ready)
		return;
	length = strlen(boot_trace);
	if (length >= sizeof(boot_trace) - 80)
		return;
	snprintf(boot_trace + length, sizeof(boot_trace) - length,
		 "%s:%d\n", stage, value);
	ret = vendor_storage_write(ROCKCHIP_BOOT_TRACE_ID, boot_trace,
				   strlen(boot_trace) + 1);
	if (ret < 0)
		printf("Boot trace write failed: %d\n", ret);
}
