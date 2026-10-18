# Pinned RK3562 rkbin inputs

This directory records the pinned RK3562 rkbin version and checksums. Firmware
archives and extracted binaries are local caches and are not committed.

- `rkbin-7e437acd9d911085d2631eca91c196bc75ae28e0.tar.gz` is the complete
  Rockchip rkbin source archive at commit
  `7e437acd9d911085d2631eca91c196bc75ae28e0`.
- `rk3562_ddr_920MHz_v1.07.bin` is the official binary from that archive. Its
  58,064 bytes are byte-identical to the non-padding portion extracted from
  the device eMMC RKNS image.
- `SHA256SUMS` records the expected checksums.

`build_uboot_rk3562.sh` reuses the local archive when present. Otherwise it
downloads that exact commit from Rockchip's rkbin repository and verifies its
SHA-256 before use. An offline build needs this archive supplied in advance,
or an explicit `RKBIN` directory / `RKBIN_ARCHIVE` path. Board-profile builds
replace DDR, BL31 and BL32 with the selected board's verified snapshots.

The cross compiler is intentionally not stored here. Override `TOOLCHAIN` or
`CROSS_COMPILE` when running `build_uboot_rk3562.sh` on another host.
