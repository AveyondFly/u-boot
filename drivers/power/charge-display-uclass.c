/*
 * (C) Copyright 2017 Rockchip Electronics Co., Ltd
 *
 * SPDX-License-Identifier:     GPL-2.0+
 */

#include <command.h>
#include <common.h>
#include <dm.h>
#include <power/charge_display.h>

#define CHARGE_TRACE_SIZE 512
#define CHARGE_TRACE_VERSION "v4-pmic"

void charge_display_trace(const char *stage, int value)
{
	const char *old = env_get("charge_trace");
	char trace[CHARGE_TRACE_SIZE];
	int len;

	len = snprintf(trace, sizeof(trace), "%s,%s:%d",
		       old ? old : CHARGE_TRACE_VERSION, stage, value);
	if (len >= sizeof(trace))
		return;
	env_set("charge_trace", trace);
}

void charge_display_add_bootargs(void)
{
	const char *trace = env_get("charge_trace");
	char argument[CHARGE_TRACE_SIZE + 16];

	if (!trace)
		return;
	snprintf(argument, sizeof(argument), "uboot.charge=%s", trace);
	env_update("bootargs", argument);
}

int charge_display_show(struct udevice *dev)
{
	const struct dm_charge_display_ops *ops = dev_get_driver_ops(dev);

	if (!ops || !ops->show)
		return -ENOSYS;

	return ops->show(dev);
}

int charge_display(void)
{
	struct udevice *dev;
	int ret;

	env_set("charge_trace", CHARGE_TRACE_VERSION);
	ret = uclass_get_device(UCLASS_CHARGE_DISPLAY, 0, &dev);
	charge_display_trace("display", ret);
	if (ret) {
		printf("Get charge display failed, ret=%d\n", ret);
		return ret;
	}

	ret = charge_display_show(dev);
	charge_display_trace("return", ret);
	return ret;
}

UCLASS_DRIVER(charge_display) = {
	.id	= UCLASS_CHARGE_DISPLAY,
	.name	= "charge_display",
};
