#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
src=(ROOT / 'drivers/power/charge_animation.c').read_text()
start=src.index('static int leds_update(')
end=src.index('\nstatic int charge_get_led(',start)
func=src[start:end]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
enum { LEDST_OFF, LEDST_ON };
struct udevice;
struct charge_animation_pdata { int charged_led_min_soc; bool charge_leds_off_at_full; };
struct charge_animation_priv { struct udevice *led_charging, *led_full; int led_soc; };
struct udevice { struct charge_animation_pdata *pdata; struct charge_animation_priv *priv; int id; };
static void *dev_get_platdata(struct udevice *d) { return d->pdata; }
static void *dev_get_priv(struct udevice *d) { return d->priv; }
static int states[2], calls, fail_next, check_overlap=1;
static int led_set_state(struct udevice *d, int state) {
 calls++;
 if (fail_next) { fail_next=0; return -EIO; }
 states[d->id]=state;
 if (check_overlap) assert(!(states[0] && states[1]));
 return 0;
}
'''
post=r'''
int main(void) {
 struct charge_animation_pdata p={90,true};
 struct udevice red={.id=0}, blue={.id=1};
 struct charge_animation_priv v={&red,&blue,-1};
 struct udevice d={&p,&v,0};
 int socs[]={0,1,89,90,99,100,99,90,89,100,0};
 int reds[]={1,1,1,0,0,0,0,0,1,0,1};
 int blues[]={0,0,0,1,1,0,1,1,0,0,0};
 for (unsigned i=0;i<ARRAY_SIZE(socs);i++) {
   assert(leds_update(&d,socs[i])==0);
   assert(states[0]==reds[i] && states[1]==blues[i]);
 }
 int before=calls;
 assert(leds_update(&d,0)==0 && calls==before);
 fail_next=1;
 assert(leds_update(&d,95)==-EIO && v.led_soc==0);
 assert(leds_update(&d,95)==0 && states[0]==0 && states[1]==1);
 assert(leds_update(&d,-1)==-EINVAL);
 assert(leds_update(&d,101)==-EINVAL);
 p.charged_led_min_soc=100; p.charge_leds_off_at_full=false; v.led_soc=-1;
 assert(leds_update(&d,99)==0 && states[0]==1 && states[1]==0);
 assert(leds_update(&d,100)==0 && states[0]==0 && states[1]==1);
 struct charge_animation_priv other={&red,&blue,-1};
 struct udevice d2={&p,&other,0};
 before=calls;
 assert(leds_update(&d2,100)==0 && calls>before);
 v.led_charging=NULL; v.led_full=NULL; v.led_soc=-1;
 assert(leds_update(&d,20)==0);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="rk3562-charge-test-") as tmp:
    p = Path(tmp) / "test.c"
    binary = Path(tmp) / "test"
    p.write_text(pre+func+post)
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare", str(p), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: 0/89/90/99/100%, reverse transitions, no mixed colours, retry, cache, per-device state, absent LEDs, legacy policy')
