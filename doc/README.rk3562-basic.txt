RK3562 additional basic boot profiles
====================================

Scope (2026-11-14)
------------------
rg43h:    AISLPC RG43H Pro
rg43v:    AISLPC RG43V Pro
rg52mini: AISLPC RG52 Mini
xf55h:    Xifan XF55H

Each model has its own profile, defconfig, control DTS and input snapshot.
The distribution v1 DTS variants of RG43H/V and RG52 Mini change Wi-Fi only;
they retain the same model, power and TF1/eMMC configuration. They use their
corresponding model profile here; Wi-Fi is not initialized by this work.

Hardware data comes from distribution's model DTS, preprocessed with the
kernel_rk356x includes and compiled with dtc -@. Exact source revisions are
recorded in each board.json hardware_source. These are distribution Linux
hardware inputs, not target-device factory SPL/U-Boot control trees.

Power and storage
-----------------
Keep each model's regulator voltages, enable GPIOs, supply connections and
battery data. In the Linux hardware inputs, TF1 is mmc@ff880000, 4-bit, with
vmmc from vcc-sd (3.3 V, GPIO0_A5 active low) and vqmmc from RK817 LDO5
(1.8..3.3 V). eMMC is mmc@ff870000, 8-bit. Both have a Linux maximum of
200 MHz; this is not evidence of their SPL/U-Boot stage configuration.

These Linux trees contain no explicit io-voltage-domain compatible node.
RK3562 has hardware voltage detection: GPIO2_IOC_IO_VSEL0 (0xff060300),
GPIO4_IOC_IO_VSEL1 (0xff070300) and GPIO0_IOC_IO_VSEL2 (0xff080148), bit 0,
select hardware detection (0, reset default) or IOC software override (1).
See Rockchip RK3562 TRM V1.0 Part1, register descriptions IO_VSEL0/1/2:
https://rockchip.fr/Rockchip%20RK3562%20TRM%20V1.0%20Part1.pdf

The profiles use the model's existing distribution regulator and MMC
supply configuration directly. No IO-domain node or IO voltage source
register writes are added. In particular, absence of an IO-domain node
is not treated as missing support: RK3562 has hardware voltage detection.
There is no target-specific evidence requiring a software source override
or an explicit restoration of the detector source. Existing hardware/firmware
selection is preserved. The RK3568 software IO-domain driver is not used.

SPL retains the requested donor's early power policy, without new PMIC
writes. In the board-DTB phase, U-Boot initializes the model's own rails
from its embedded DTB. LDO ranges and SD supply targets are explicitly
checked against that model's input.

Preserved battery identity (design_capacity / design_qmax / bat_res):
  RG43H/V: 2906 / 3197 / 102
  RG52 Mini: 4654 / 5119 / 78
  XF55H: 3727 / 4100 / 217

No game-controller or display support is added. Display drivers are disabled.
All four profiles enable screen-off charging with RK817 PMIC USB-presence
detection, input-path initialization, no automatic SOC exit, and each device's
existing red/green power LED nodes. Each model's own OCV/capacity/resistance
and charger settings remain unchanged. Other Linux hardware nodes remain in the
input so their regulator dependencies are preserved. No early TF2/Wi-Fi
initialization is added.

Provisional boot references -- explicitly requested by the user
-------------------------------------------------------------
Target-device original IDB/SPL and U-Boot FIT were unavailable. The user
explicitly requested reuse of existing firmware AND early MMC settings,
with target hardware validation deferred:
  RG43H/V, RG52 Mini: Pocket AIS DDR/BL31/BL32 and SPL/U-Boot MMC reference.
  XF55H: NGP45H DDR/BL31/BL32 and SPL/U-Boot MMC reference.
This donor selection is provisional; it is not a compatibility finding.
The two donors' DDR, BL31 and BL32 payloads are NOT byte-identical.
The donor names, firmware input SHA-256 and separate SPL/U-Boot control-DTB
SHA-256 are pinned in each board.json. Input files use reference-* names
so that they cannot be mistaken for factory captures of the new model.

Keep SPL and U-Boot MMC stage differences, TF1 before eMMC, --spl-new
packaging, final IDB re-extraction, compiled SPL/control-DTB comparison,
and DDR/BL31/BL32 payload validation. Passing these checks establishes
that the requested donor reference was preserved; it does NOT establish
that the target hardware can boot it.

Validation status
-----------------
All seven distribution Linux DTS inputs (four models plus three v1 variants)
compile with cpp/dtc. Normalized power and TF1/eMMC configuration comparisons
confirm each v1 matches its base, and RG43H/V match each other in these areas.

Hardware tests for EVERY new model:
  TF1 boot: not tested.
  eMMC-only cold boot: not tested.
  Charging: all four models enable screen-off charging; hardware
            behavior remains unverified pending individual device tests.

Build with ./build_uboot_rk3562_board.sh --board <id>.
Do not report build checks or borrowed firmware hashes as hardware tests.
Before claiming hardware support, capture the actual working target boot
chain, compare both early stages, and test these scenarios separately.

Additional host validation
--------------------------
Profile checks pin each LDO range and TF1 vmmc/vqmmc regulator targets;
power comparison preserves the complete input configuration. These are
build/static checks, not hardware voltage measurements.

2026-11-14 XF55H charging regression
-----------------------------------
The updater result 20261114T034614.440539Z confirmed successful write and
readback of image 42d30bdd5712cb5c4429a3305774ae4a87f68745f9d5f9b19d8186e81a784a62.
The running XF55H reported enabled:0, mode:11, on-src:64, online:0 in its
uboot.charge trace. This was a configuration regression: the basic overlay
disabled the charging loop, rather than an updater failure. The revised
overlay enables screen-off charging and bypasses the inherited GPIO0_B3 DC
detector in favor of RK817 USB presence, following the existing v4-pmic
implementation. Actual charging/LED behavior still requires a user retest.
A normal Linux boot with this model/firmware trace was observed; TF1 and
eMMC-only cold boot scenarios have not been tested separately.

XF55H comparison with NGP45H (2026-11-14)
----------------------------------------
Compared the generated charging DTBs, not only the overlay source. The entire
charge-animation node matches NGP45H byte-for-byte except for the two LED
phandles, which point to each device's own LED nodes. PMIC detection source,
USB input-path initialization and the 3000 ms detection timeout also match.
The defconfigs differ only in DEFAULT_DEVICE_TREE and disabled display
drivers. Required charging, RK817, GPIO, IRQ, suspend and LED drivers are
enabled in the compiled XF55H configuration. LED_GPIO is now also required
by the profile validator. A host regression compares both overlays on a
fixture while checking that XF55H battery/charger data is preserved.

The current repaired XF55H image already satisfies the strengthened checks;
this audit adds validation, without changing its runtime charging policy.
GPIO3_A1 red / GPIO3_A0 green, own OCV/capacity/resistance, regulator data
and SD supplies are intentional board differences. The shared DDR/BL31/BL32
and early MMC reference remain provisional per user instruction.

RG43H/V and RG52 Mini have now been synchronized with the XF55H fix. All
four overlays are checked against NGP45H for charging policy while preserving
their separate board inputs, LED wiring, battery data and charger limits.
This is build-time validation; hardware charging, plug removal, key handling
and LED transitions still require separate tests on every device.

Synchronized charging profiles
------------------------------
RG43H/V and RG52 Mini now use the same screen-off charging policy as
XF55H/NGP45H: PMIC presence detection with 3000 ms initial wait, USB input
path initialization, no automatic charge exit, 5-second wakeup, 90% LED
transition and both LEDs off at full SOC. Their own red/green GPIO3_A1/A0
LED nodes are reused, without introducing duplicate GPIO consumers.
LED_GPIO and all charging output properties are required build checks.
The regression fixture exercises all four independent overlays and keeps
each device's battery/charger properties unchanged.

Preserved charger limits (max_chrg_voltage mV / max_chrg_current mA):
  RG43H/V: 4280 / 1500
  RG52 Mini: 4300 / 2000
  XF55H: 4350 / 2000
These are the source DT properties, not hardware measurements. No IO-domain
register writes, display/controller support or early TF2/Wi-Fi initialization
are added by this synchronization.
