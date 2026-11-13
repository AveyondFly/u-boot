// SPDX-License-Identifier: GPL-2.0+
/* Deliberately enabled only for handheld boot diagnostics. */
#include <common.h>
#include <version.h>
#include <membuff.h>
#include <asm/arch/boot_trace.h>
#include <asm/arch/vendor.h>

DECLARE_GLOBAL_DATA_PTR;

/* Stages plus the console's 8 KiB output buffer fit in an MMC vendor item. */
static char boot_trace[12288];
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
	/* Reserve the rest for console output after a failed boot command. */
	if (length >= 2048 - 80)
		return;
	snprintf(boot_trace + length, sizeof(boot_trace) - length,
		 "%s:%d\n", stage, value);
	ret = vendor_storage_write(ROCKCHIP_BOOT_TRACE_ID, boot_trace,
				   strlen(boot_trace) + 1);
	if (ret < 0)
		printf("Boot trace write failed: %d\n", ret);
}

void rockchip_boot_trace_console(void)
{
	struct membuff *output;
	unsigned long flags;
	size_t length;
	int ch;

	if (!trace_ready || !gd || !gd->console_out.start)
		return;
	output = (struct membuff *)&gd->console_out;
	flags = gd->flags;
	/* Vendor storage errors may print; never feed those back into this loop. */
	gd->flags &= ~GD_FLG_RECORD;
	length = strlen(boot_trace);
	if (length + sizeof("console-output:\n") < sizeof(boot_trace)) {
		strcpy(boot_trace + length, "console-output:\n");
		length += strlen("console-output:\n");
		while (length < sizeof(boot_trace) - 1 &&
		       (ch = membuff_getbyte(output)) != -1)
			boot_trace[length++] = ch;
		boot_trace[length] = '\0';
		vendor_storage_write(ROCKCHIP_BOOT_TRACE_ID, boot_trace, length + 1);
	}
	gd->flags = flags;
}
