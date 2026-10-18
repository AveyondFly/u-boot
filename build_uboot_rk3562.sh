#!/usr/bin/env bash
# RK3562 FIT/IDB backend. Board profiles provide their own firmware and config.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFCONFIG="${DEFCONFIG:-rk3562_defconfig}"
DEFCONFIG_FILE="${DEFCONFIG_FILE:-${ROOT_DIR}/configs/${DEFCONFIG}}"
SOURCE_OVERLAY="${SOURCE_OVERLAY:-}"
EARLY_DTB="${EARLY_DTB:-}"
TOOLCHAIN="${TOOLCHAIN:-/home/ubuntu/distribution/build.ROCKNIX-RK3326.aarch64/toolchain}"
CROSS_COMPILE="${CROSS_COMPILE:-${TOOLCHAIN}/bin/aarch64-rocknix-linux-gnu-}"
PYTHON="${PYTHON:-python3}"
JOBS="${JOBS:-$(nproc)}"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/build-rk3562}"

# This is the first official rkbin revision after RK3562 SPL v1.07 and before
# BL32 was updated to v1.08.  It contains the firmware versions found in the
# reference dump: DDR v1.07, BL31 v1.21 and BL32 v1.07.
RKBIN_VERSION="${RKBIN_VERSION:-7e437acd9d911085d2631eca91c196bc75ae28e0}"
RKBIN_SHA256="${RKBIN_SHA256:-8d8930b585283ce94b73299985f7b7d88db88500caf6a6c1e0e8f505c3e0c56a}"
RKBIN_URL="${RKBIN_URL:-https://github.com/rockchip-linux/rkbin/archive/${RKBIN_VERSION}.tar.gz}"
RKBIN_LOCAL_ARCHIVE="${ROOT_DIR}/third_party/rkbin/rkbin-${RKBIN_VERSION}.tar.gz"
RKBIN_ARCHIVE="${RKBIN_ARCHIVE:-${RKBIN_LOCAL_ARCHIVE}}"
BUNDLED_DDR_BIN="${ROOT_DIR}/third_party/rkbin/rk3562_ddr_920MHz_v1.07.bin"

# Optional overrides for board-specific firmware.  Files are copied into the
# temporary rkbin tree, so the source rkbin checkout is never modified.
DDR_BIN="${DDR_BIN:-}"
IDB_IMAGE="${IDB_IMAGE:-}"
BL31_BIN="${BL31_BIN:-}"
BL32_BIN="${BL32_BIN:-}"
RKBIN="${RKBIN:-}"
RKBIN_SOURCE="${RKBIN:-${RKBIN_ARCHIVE}}"
KEEP_WORK="${KEEP_WORK:-0}"

# The eMMC dump contains the official 920 MHz v1.07 binary. Prefer the local
# byte-identical copy unless the caller supplies DDR_BIN or IDB_IMAGE.
if [ -z "${DDR_BIN}" ] && [ -z "${IDB_IMAGE}" ] && [ -f "${BUNDLED_DDR_BIN}" ]; then
	DDR_BIN="${BUNDLED_DDR_BIN}"
fi

WORK_DIR=""

usage() {
	cat <<EOF
Usage: $(basename "$0")

Builds RK3562 IDB and FIT images into build-rk3562/ without touching the
current in-tree build. The bundled rkbin revision provides BL31 v1.21 and
BL32 v1.07 as reference defaults. Board profiles override DDR/BL31/BL32 with
their own verified vendor payloads. The bundled DDR matches the older RG43H dump.

Useful environment overrides:
  TOOLCHAIN, CROSS_COMPILE, JOBS, OUT_DIR
  DEFCONFIG_FILE        Generated board defconfig (copied into staging only)
  SOURCE_OVERLAY        Extra board source files, relative to U-Boot root
  EARLY_DTB             Board DTB to embed at dts/rk3562-charge.dtb
  RKBIN                 Existing rkbin directory; it is copied before use
  RKBIN_ARCHIVE         Existing rkbin archive (defaults to third_party/rkbin)
  DDR_BIN               Board-specific DDR binary
  IDB_IMAGE             RKNS IDB area dump; verified image 0 replaces DDR_BIN
  BL31_BIN, BL32_BIN    Trusted firmware overrides
  KEEP_WORK=1           Keep the temporary source and rkbin trees
EOF
}

log() {
	printf '[rk3562] %s\n' "$*"
}

die() {
	printf '[rk3562] ERROR: %s\n' "$*" >&2
	exit 1
}

cleanup() {
	if [ "${KEEP_WORK}" = "1" ]; then
		[ -z "${WORK_DIR}" ] || log "Temporary build kept at ${WORK_DIR}"
	elif [ -n "${WORK_DIR}" ] && [ -d "${WORK_DIR}" ]; then
		rm -rf -- "${WORK_DIR}"
	fi
}
trap cleanup EXIT

require_command() {
	command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

verify_archive() {
	local archive="$1"
	local actual
	actual="$(sha256sum "${archive}" | awk '{print $1}')"
	[ "${actual}" = "${RKBIN_SHA256}" ] || \
		die "rkbin checksum mismatch: ${actual} (expected ${RKBIN_SHA256})"
}

fetch_rkbin_archive() {
	local part

	if [ -s "${RKBIN_ARCHIVE}" ]; then
		verify_archive "${RKBIN_ARCHIVE}"
		return
	fi

	mkdir -p "$(dirname "${RKBIN_ARCHIVE}")"
	part="${RKBIN_ARCHIVE}.part.$$"
	log "Downloading rkbin ${RKBIN_VERSION}"
	if ! curl -L --fail --retry 3 -o "${part}" "${RKBIN_URL}"; then
		rm -f -- "${part}"
		die "Failed to download ${RKBIN_URL}"
	fi
	verify_archive "${part}"
	mv -f -- "${part}" "${RKBIN_ARCHIVE}"
}

stage_rkbin() {
	local extracted

	if [ -n "${RKBIN}" ]; then
		[ -d "${RKBIN}" ] || die "RKBIN directory not found: ${RKBIN}"
		log "Staging rkbin from ${RKBIN}"
		cp -a -- "${RKBIN}" "${WORK_DIR}/rkbin"
	else
		fetch_rkbin_archive
		log "Extracting ${RKBIN_ARCHIVE}"
		tar -xzf "${RKBIN_ARCHIVE}" -C "${WORK_DIR}"
		extracted="${WORK_DIR}/rkbin-${RKBIN_VERSION}"
		[ -d "${extracted}" ] || die "Unexpected rkbin archive layout"
		mv -- "${extracted}" "${WORK_DIR}/rkbin"
	fi

	RKBIN="${WORK_DIR}/rkbin"
	[ -x "${RKBIN}/tools/boot_merger" ] || die "rkbin boot_merger is missing"
	[ -f "${RKBIN}/RKBOOT/RK3562MINIALL.ini" ] || die "RK3562 loader INI is missing"
	[ -f "${RKBIN}/RKTRUST/RK3562TRUST.ini" ] || die "RK3562 trust INI is missing"
}

install_firmware_override() {
	local source="$1"
	local destination="$2"
	local ini="$3"
	local pattern="$4"

	[ -f "${source}" ] || die "Firmware override not found: ${source}"
	mkdir -p "$(dirname "${RKBIN}/${destination}")"
	cp -f -- "${source}" "${RKBIN}/${destination}"
	sed -i "s|${pattern}|${destination}|g" "${ini}"
}

apply_firmware_overrides() {
	local loader_ini="${RKBIN}/RKBOOT/RK3562MINIALL.ini"
	local trust_ini="${RKBIN}/RKTRUST/RK3562TRUST.ini"

	if [ -n "${DDR_BIN}" ]; then
		install_firmware_override "${DDR_BIN}" \
			"bin/custom/rk3562_ddr.bin" "${loader_ini}" \
			"bin/rk35/rk3562_ddr_[^[:space:]]*\.bin"
	fi
	if [ -n "${BL31_BIN}" ]; then
		install_firmware_override "${BL31_BIN}" \
			"bin/custom/rk3562_bl31_custom.elf" "${trust_ini}" \
			"bin/rk35/rk3562_bl31_[^[:space:]]*\.elf"
	fi
	if [ -n "${BL32_BIN}" ]; then
		install_firmware_override "${BL32_BIN}" \
			"bin/custom/rk3562_bl32_custom.bin" "${trust_ini}" \
			"bin/rk35/rk3562_bl32_[^[:space:]]*\.bin"
	fi
}

extract_dumped_ddr() {
	local output="${WORK_DIR}/rk3562-ddr-from-idb.bin"

	[ -f "${IDB_IMAGE}" ] || die "IDB image not found: ${IDB_IMAGE}"
	log "Extracting and verifying DDR payload from ${IDB_IMAGE}"
	"${PYTHON}" "${ROOT_DIR}/scripts/extract_rockchip_rkns.py" \
		--image 0 "${IDB_IMAGE}" "${output}"
	DDR_BIN="${output}"
}

stage_source() {
	local build_source="${WORK_DIR}/u-boot"

	mkdir -p "${build_source}"
	# Copy tracked files from the working tree, including tracked local changes,
	# while excluding stale in-tree build products and unrelated dump files.
	(
		cd "${ROOT_DIR}"
		git ls-files -z | tar --null -T - -cf -
	) | tar -xf - -C "${build_source}"
	# Board defconfigs may still be untracked during local bring-up.
	install -m 0644 "${DEFCONFIG_FILE}" \
		"${build_source}/configs/${DEFCONFIG}"
	if [ -n "${SOURCE_OVERLAY}" ]; then
		[ -d "${SOURCE_OVERLAY}" ] || die "Source overlay not found: ${SOURCE_OVERLAY}"
		cp -a -- "${SOURCE_OVERLAY}/." "${build_source}/"
	fi
	if [ -n "${EARLY_DTB}" ]; then
		install -m 0644 "${EARLY_DTB}" "${build_source}/dts/rk3562-charge.dtb"
	fi
	printf '%s\n' "${build_source}"
}

validate_firmware_set() {
	local loader_ini="${RKBIN}/RKBOOT/RK3562MINIALL.ini"
	local trust_ini="${RKBIN}/RKTRUST/RK3562TRUST.ini"
	local relative

	for relative in \
		"$(sed -n 's/^FlashData=//p' "${loader_ini}" | tr -d '\r')" \
		"$(sed -n '/_bl31_/s/^PATH=//p' "${trust_ini}" | tr -d '\r')" \
		"$(sed -n '/_bl32_/s/^PATH=//p' "${trust_ini}" | tr -d '\r')"; do
		[ -f "${RKBIN}/${relative}" ] || die "Required rkbin firmware is missing: ${relative}"
	done
}

build_images() {
	local build_source="$1"
	local loader_ini="${RKBIN}/RKBOOT/RK3562MINIALL.ini"
	local trust_ini="${RKBIN}/RKTRUST/RK3562TRUST.ini"

	log "Building ${DEFCONFIG} with ${CROSS_COMPILE}gcc"
	(
		cd "${build_source}"
		export CCACHE_DISABLE=1
		export RKBIN
		export RKBIN_TOOLS="${RKBIN}/tools"
		export PYTHON JOBS
		./make.sh "${DEFCONFIG%_defconfig}" --spl \
			"${loader_ini}" "${trust_ini}" \
			"CROSS_COMPILE=${CROSS_COMPILE}"
	)
}

publish_images() {
	local build_source="$1"
	local loader
	local idblock="${RKBIN}/idblock.img"
	local idblock_size
	local image_size
	local ddr_relative
	local half_size=$((2 * 1024 * 1024))
	local final_tmp="${WORK_DIR}/rk3562-uboot-16m.bin"

	loader="$(find "${build_source}" -maxdepth 1 -type f \
		-name 'rk3562*loader*.bin' -print -quit)"
	[ -n "${loader}" ] || die "RK3562 loader output was not generated"
	[ -f "${idblock}" ] || die "RK3562 idblock output was not generated"
	[ -f "${build_source}/uboot.img" ] || die "uboot.img was not generated"

	idblock_size="$(stat -c '%s' "${idblock}")"
	[ "${idblock_size}" -le $((512 * 1024)) ] || \
		die "idblock.img does not fit in a 512 KiB redundant slot: ${idblock_size}"
	image_size="$(stat -c '%s' "${build_source}/uboot.img")"
	[ "${image_size}" -eq $((4 * 1024 * 1024)) ] || \
		die "Unexpected uboot.img size: ${image_size}"
	cmp -s -n "${half_size}" "${build_source}/uboot.img" \
		<(
			dd if="${build_source}/uboot.img" bs="${half_size}" skip=1 count=1 status=none
		) || die "The two 2 MiB FIT copies in uboot.img differ"

	mkdir -p "${OUT_DIR}"
	install -m 0644 "${idblock}" "${OUT_DIR}/idblock.img"
	install -m 0644 "${loader}" "${OUT_DIR}/rk3562-download-loader.bin"
	install -m 0644 "${build_source}/uboot.img" "${OUT_DIR}/uboot.img"
	install -m 0644 "${build_source}/fit/uboot.itb" "${OUT_DIR}/uboot.itb"
	install -m 0644 "${build_source}/u-boot-nodtb.bin" "${OUT_DIR}/u-boot-nodtb.bin"
	install -m 0644 "${build_source}/u-boot.dtb" "${OUT_DIR}/u-boot.dtb"
	install -m 0644 "${build_source}/spl/u-boot-spl.dtb" "${OUT_DIR}/u-boot-spl.dtb"
	local control_name
	control_name="$(sed -n 's/^CONFIG_DEFAULT_DEVICE_TREE="\(.*\)"/\1/p' "${build_source}/.config")"
	install -m 0644 "${build_source}/arch/arm/dts/${control_name}.dtb" \
		"${OUT_DIR}/u-boot-control-full.dtb"
	install -m 0644 "${build_source}/.config" "${OUT_DIR}/u-boot.config"
	install -m 0644 "${build_source}/spl/u-boot-spl.bin" "${OUT_DIR}/u-boot-spl.bin"
	ddr_relative="$(sed -n 's/^FlashData=//p' \
		"${RKBIN}/RKBOOT/RK3562MINIALL.ini" | tr -d '\r')"
	[ -f "${RKBIN}/${ddr_relative}" ] || \
		die "DDR binary used by the build is missing: ${ddr_relative}"
	install -m 0644 "${RKBIN}/${ddr_relative}" "${OUT_DIR}/rk3562-ddr.bin"

	# This is the pre-partition blob written at disk sector 64.  Store redundant
	# IDB copies 512 KiB apart, as in the reference dump.  The FIT starts at
	# blob sector 16320, which lands at disk sector 16384 (8 MiB).
	# Since this blob is written at disk sector 64, stop at disk sector 32768
	# instead of overrunning the first data partition by 32 KiB.
	truncate -s $((16 * 1024 * 1024 - 64 * 512)) "${final_tmp}"
	dd if="${idblock}" of="${final_tmp}" bs=512 conv=notrunc status=none
	dd if="${idblock}" of="${final_tmp}" bs=512 seek=1024 conv=notrunc status=none
	dd if="${build_source}/uboot.img" of="${final_tmp}" \
		bs=512 seek=16320 conv=notrunc status=none
	install -m 0644 "${final_tmp}" "${OUT_DIR}/rk3562-uboot-16m.bin"

	"${build_source}/tools/dumpimage" -l "${build_source}/uboot.img" \
		>"${OUT_DIR}/uboot-fit.txt"
	grep -q 'OP-TEE' "${OUT_DIR}/uboot-fit.txt" || die "OP-TEE is absent from the FIT"
	grep -q 'ARM Trusted Firmware' "${OUT_DIR}/uboot-fit.txt" || die "BL31 is absent from the FIT"

	{
		printf 'rkbin_commit=%s\n' "${RKBIN_VERSION}"
		printf 'rkbin_source=%s\n' "${RKBIN_SOURCE}"
		printf 'defconfig=%s\n' "${DEFCONFIG}"
		printf 'cross_compile=%s\n' "${CROSS_COMPILE}"
		printf 'ddr_source=%s\n' "${IDB_IMAGE:-${DDR_BIN:-${ddr_relative}}}"
		printf 'loader_source=%s\n' "$(basename "${loader}")"
		sha256sum \
			"${OUT_DIR}/idblock.img" \
			"${OUT_DIR}/rk3562-download-loader.bin" \
			"${OUT_DIR}/uboot.img" \
			"${OUT_DIR}/rk3562-uboot-16m.bin" \
			"${OUT_DIR}/u-boot-nodtb.bin" \
			"${OUT_DIR}/u-boot.dtb" \
			"${OUT_DIR}/u-boot-spl.bin"
		sha256sum "${OUT_DIR}/rk3562-ddr.bin"
	} >"${OUT_DIR}/manifest.txt"

	log "Build complete: ${OUT_DIR}/rk3562-uboot-16m.bin"
}

main() {
	local build_source

	case "${1:-}" in
		-h|--help)
			usage
			return
			;;
		"")
			;;
		*)
			die "Unknown argument: $1"
			;;
	esac

	require_command git
	require_command tar
	require_command curl
	require_command sha256sum
	require_command "${PYTHON}"
	[ -x "${CROSS_COMPILE}gcc" ] || die "Cross compiler not found: ${CROSS_COMPILE}gcc"
	[ -f "${DEFCONFIG_FILE}" ] || die "Defconfig not found: ${DEFCONFIG_FILE}"
	[ -z "${IDB_IMAGE}" ] || [ -z "${DDR_BIN}" ] || \
		die "Use only one of IDB_IMAGE and DDR_BIN"

	WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rk3562-uboot.XXXXXX")"
	if [ -n "${IDB_IMAGE}" ]; then
		extract_dumped_ddr
	fi
	stage_rkbin
	apply_firmware_overrides
	validate_firmware_set
	build_source="$(stage_source)"
	build_images "${build_source}"
	publish_images "${build_source}"
}

main "$@"
