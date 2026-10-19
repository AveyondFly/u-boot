# RK3562 device support

For RK3562 changes, follow `doc/README.rk3562-charge.txt` and these rules:

- Give every model its own profile, defconfig, control DTS and vendor inputs.
  Preserve its own DDR, BL31, BL32, regulators, battery data and charging GPIOs.
- Obtain early MMC settings from that device's original IDB/SPL and U-Boot FIT.
  Compare SPL and U-Boot separately; their modes can differ. A Linux DTB is not
  a substitute for either boot-stage DTB. An EVB model string alone does not
  establish whether the actual hardware settings are shared.
- Keep TF1 before eMMC in the boot order. Support TF1 boot and eMMC boot without
  TF1. Do not add early TF2 or Wi-Fi initialization as part of this requirement.
- Preserve the observed early power policy. Do not copy another board's LDO
  voltages or add early PMIC writes solely to make the trees look alike.
- FIT packaging must use `--spl-new`. Re-extract SPL from the final IDB and
  compare it with the compiled SPL, including its control DTB. Preserve the
  original DDR and trusted-firmware payloads and run the board validators.
- Register the exact DT model in `tools/ports/uboot/devices.json` and add its
  input archive to the CI input Release. The combined Ports Release must contain
  every registered profile, with verified images from the same source revision.
- Keep Ports archives extractable directly into `/roms/ports/`, with
  `Update U-Boot.sh` and `uboot/` at the archive root. Preserve runtime backups
  and logs when installing an update.
- Distinguish build checks from hardware tests. Record the device and tested
  scenarios; report TF1 boot, eMMC-only cold boot and charging tests separately.

Run the board, control-DTB, CI, Ports updater and Ports package tests for changes
to this build/release path. Do not include unrelated RK3326 work or local dumps.
