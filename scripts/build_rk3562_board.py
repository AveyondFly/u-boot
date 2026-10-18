#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Build isolated RK3562 board profiles from saved vendor inputs."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from extract_rk3562_fit_firmware import extract as extract_fit, integer, prop
from extract_rockchip_rkns import extract as extract_idb

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "board/rockchip/rk3562-profiles"
DEFAULT_CROSS = "/opt/toolchains/gcc-linaro-6.3.1-2017.05-x86_64_aarch64-linux-gnu/bin/aarch64-linux-gnu-"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT, **kwargs)


def load_profile(path):
    profile = json.loads(path.read_text())
    if profile.get("schema_version") != 1:
        raise ValueError("unsupported board profile schema")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", profile.get("id", "")):
        raise ValueError("board id must contain lowercase letters, digits, _ or -")
    if bool(profile["inputs"].get("linux_dtb")) == bool(profile["inputs"].get("linux_dts")):
        raise ValueError("provide exactly one of inputs.linux_dtb or linux_dts")
    if not profile.get("overlays"):
        raise ValueError("provide board charging overlays explicitly")
    return profile


def input_file(data_dir, name):
    path = (data_dir / name).resolve()
    if not path.is_relative_to(data_dir.resolve()):
        raise ValueError("input path escapes the board input directory: " + name)
    if not path.is_file():
        raise ValueError("missing board input: " + str(path))
    return path


def verify_snapshot(data_dir, board_id):
    path = data_dir / "snapshot.json"
    if not path.exists():
        return
    snapshot = json.loads(path.read_text())
    if snapshot["board_id"] != board_id:
        raise ValueError("snapshot belongs to a different board")
    for name, digest in snapshot["sha256"].items():
        if sha256(input_file(data_dir, name)) != digest:
            raise ValueError("snapshot checksum mismatch: " + name)


def check_properties(dtb, checks):
    for check in checks:
        node, name = check["node"], check["property"]
        if "string" in check:
            actual = prop(dtb, node, name, "s")
            expected = check["string"]
        else:
            actual = [int(word, 16) for word in prop(dtb, node, name).split()]
            if "gpio_controller" in check:
                expected = [integer(dtb, check["gpio_controller"], "phandle"),
                            check["pin"], check.get("flags", 0)]
            else:
                expected = check["cells"]
        if actual != expected:
            raise ValueError(f"board DTB mismatch: {node}:{name}: {actual!r} != {expected!r}")


def prepare_dtb(profile, profile_dir, data_dir, stage):
    inputs = profile["inputs"]
    source = input_file(data_dir, inputs.get("linux_dtb") or inputs["linux_dts"])
    base = source
    if "linux_dts" in inputs:
        base = stage / "base.dtb"
        run("dtc", "-@", "-I", "dts", "-O", "dtb", "-i", source.parent,
            "-o", base, source)
    if "rockchip,rk3562" not in prop(base, "/", "compatible", "s").split():
        raise ValueError("base DTB is not compatible with rockchip,rk3562")
    check_properties(base, profile.get("checks", []))
    overlays = []
    sources = [source]
    for index, name in enumerate(profile["overlays"]):
        overlay_source = (profile_dir / name).resolve()
        target = stage / f"charge-{index}.dtbo"
        run("dtc", "-@", "-I", "dts", "-O", "dtb", "-i", profile_dir,
            "-o", target, overlay_source)
        overlays.append(target)
        sources.append(overlay_source)
    dtb = stage / "rk3562-charge.dtb"
    run("fdtoverlay", "-i", base, "-o", dtb, *overlays)
    check_properties(dtb, profile.get("output_checks", []))
    return dtb, overlays, sources


def render_config(profile, mode, path):
    base = ROOT / "configs" / profile["defconfig"]
    values = {}
    for line in base.read_text().splitlines():
        match = re.fullmatch(r"CONFIG_(\w+)=(.*)", line)
        disabled = re.fullmatch(r"# CONFIG_(\w+) is not set", line)
        if match:
            values[match[1]] = match[2]
        elif disabled:
            values[disabled[1]] = "n"
    for key, value in profile.get("kconfig", {}).items():
        if not re.fullmatch(r"[A-Z0-9_]+", key):
            raise ValueError("invalid Kconfig key: " + key)
        values[key] = ("y" if value else "n") if isinstance(value, bool) else (
            json.dumps(value) if isinstance(value, str) else str(value))
    values.update({
        "ROCKCHIP_EARLY_DISTRO_DTB": "y" if mode == "external" else "n",
        "ROCKCHIP_EARLY_DISTRO_DTB_PATH": json.dumps(profile.get("dtb_path", "/rk3562-charge.dtb")),
        "EMBED_KERNEL_DTB": "y" if mode == "embedded" else "n",
        "EMBED_KERNEL_DTB_ALWAYS": "y" if mode == "embedded" else "n",
        "EMBED_KERNEL_DTB_PATH": '"dts/rk3562-charge.dtb"',
    })
    path.write_text("".join(f"# CONFIG_{key} is not set\n" if value == "n"
                            else f"CONFIG_{key}={value}\n"
                            for key, value in sorted(values.items())))
    return base


def fit_payload(image, name):
    node = "/images/" + name
    offset, size = integer(image, node, "data-position"), integer(image, node, "data-size")
    return image.read_bytes()[offset:offset + size]


def build(profile, profile_path, data_dir, output, mode, dtb_only):
    profile_dir = profile_path.parent
    verify_snapshot(data_dir, profile["id"])
    if output == data_dir or output.is_relative_to(data_dir) or data_dir.is_relative_to(output):
        raise ValueError("output and vendor input directories must be separate")
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / ("." + output.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if output.exists() and any(output.iterdir()):
            previous = output / "board-build.json"
            if not previous.exists() or json.loads(previous.read_text())["board_id"] != profile["id"]:
                raise ValueError("refusing to replace an output directory not owned by this board")
        with tempfile.TemporaryDirectory(prefix="." + profile["id"] + "-", dir=output.parent) as tmp:
            stage = Path(tmp)
            artifacts = stage / "artifacts"
            artifacts.mkdir()
            dtb, overlays, sources = prepare_dtb(profile, profile_dir, data_dir, stage)
            if mode == "embedded" and dtb.stat().st_size > 512 * 1024:
                raise ValueError("embedded DTB exceeds the existing 512 KiB U-Boot buffer")
            sources.append(profile_path)
            source_tree = profile_dir / profile.get("source_overlay", "source")
            if source_tree.is_dir():
                sources.extend(sorted(p for p in source_tree.rglob("*") if p.is_file()))
            if not dtb_only:
                # Every full board build needs explicit vendor IDB and FIT.
                # The backend's generic firmware fallbacks cannot leak into this path.
                idb = input_file(data_dir, profile["inputs"]["idb"])
                uboot = input_file(data_dir, profile["inputs"]["uboot"])
                sources.extend([idb, uboot])
                extract_fit(uboot, stage / "firmware")
                extract_idb(idb, stage / "original-ddr.bin", 0)
                config = stage / "board_defconfig"
                sources.append(render_config(profile, mode, config))
                env = os.environ.copy()
                for key in ("DDR_BIN", "IDB_IMAGE", "BL31_BIN", "BL32_BIN", "EARLY_DTB", "SOURCE_OVERLAY"):
                    env.pop(key, None)
                env.update({
                    "DEFCONFIG": "rk3562_profile_defconfig", "DEFCONFIG_FILE": str(config),
                    "IDB_IMAGE": str(idb), "BL31_BIN": str(stage / "firmware/rk3562_bl31_vendor.elf"),
                    "BL32_BIN": str(stage / "firmware/optee.bin"), "OUT_DIR": str(artifacts),
                    "CROSS_COMPILE": os.environ.get("CROSS_COMPILE", DEFAULT_CROSS),
                })
                if source_tree.is_dir():
                    env["SOURCE_OVERLAY"] = str(source_tree)
                if mode == "embedded":
                    env["EARLY_DTB"] = str(dtb)
                with (stage / "build.log").open("w") as log:
                    try:
                        run(ROOT / "build_uboot_rk3562.sh", env=env, stdout=log, stderr=subprocess.STDOUT)
                    except subprocess.CalledProcessError:
                        print((stage / "build.log").read_text()[-12000:], file=sys.stderr)
                        raise
                shutil.copy2(stage / "build.log", artifacts / "build.log")
                extract_fit(artifacts / "uboot.img", stage / "rebuilt-firmware")
                expected = (stage / "firmware/firmware.json").read_bytes()
                if expected != (stage / "rebuilt-firmware/firmware.json").read_bytes():
                    raise ValueError("rebuilt BL31/BL32 differs from this board's vendor firmware")
                (artifacts / "vendor-firmware.json").write_bytes(expected)
                extract_idb(artifacts / "idblock.img", stage / "rebuilt-ddr.bin", 0)
                if (stage / "original-ddr.bin").read_bytes() != (stage / "rebuilt-ddr.bin").read_bytes():
                    raise ValueError("rebuilt DDR differs from this board's vendor DDR")
                if mode == "embedded" and fit_payload(artifacts / "uboot.img", "kern-fdt") != dtb.read_bytes():
                    raise ValueError("embedded charging DTB differs from the generated DTB")
                cfg = (artifacts / "u-boot.config").read_text()
                for key in ["ROCKCHIP_RK3562", "CHARGE_ANIMATION", "DM_KEY", "LED"] + profile.get("required_kconfig", []):
                    if f"CONFIG_{key}=y\n" not in cfg:
                        raise ValueError("required Kconfig option disabled: " + key)
                required = "CONFIG_EMBED_KERNEL_DTB_ALWAYS=y\n" if mode == "embedded" else "CONFIG_ROCKCHIP_EARLY_DISTRO_DTB=y\n"
                if required not in cfg:
                    raise ValueError("requested DTB mode did not survive Kconfig")
            shutil.copy2(dtb, artifacts / dtb.name)
            for overlay in overlays:
                shutil.copy2(overlay, artifacts / overlay.name)
            with (artifacts / "dtc-warnings.log").open("w") as warnings:
                run("dtc", "-I", "dtb", "-O", "dts", "-o", artifacts / "rk3562-charge.dts",
                    dtb, stderr=warnings)
            if not dtb_only:
                shutil.copy2(config, artifacts / "requested_defconfig")
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            git_diff = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=ROOT)
            metadata = {
                "schema_version": 1, "board_id": profile["id"], "dtb_mode": mode,
                "dtb_only": dtb_only, "built_at": datetime.now(timezone.utc).isoformat(),
                "source_commit": git_commit,
                "tracked_source_diff_sha256": hashlib.sha256(git_diff).hexdigest(),
                "build_tools": {name: sha256(ROOT / name) for name in (
                    "scripts/build_rk3562_board.py", "build_uboot_rk3562.sh",
                    "scripts/extract_rk3562_fit_firmware.py", "scripts/extract_rockchip_rkns.py")},
                "dtb_install_path": profile.get("dtb_path", "/rk3562-charge.dtb") if mode == "external" else None,
                "inputs": [{"path": str(path), "sha256": sha256(path)} for path in sources],
                "artifacts": {p.name: sha256(p) for p in artifacts.iterdir() if p.is_file()},
            }
            if (data_dir / "snapshot.json").exists():
                metadata["snapshot_sha256"] = sha256(data_dir / "snapshot.json")
            (artifacts / "board-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
            # Publish only after all checks pass; retain previous output on failure.
            previous = stage / "previous"
            if output.exists():
                output.rename(previous)
            try:
                artifacts.rename(output)
            except OSError:
                if previous.exists():
                    previous.rename(output)
                raise
    print(f"Board {profile['id']} ({mode}) ready: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board")
    parser.add_argument("--profile", type=Path, help="explicit board.json for a new board")
    parser.add_argument("--list-boards", action="store_true")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dtb-mode", choices=("external", "embedded"),
                        help="default: embedded; external must be explicitly selected")
    parser.add_argument("--dtb-only", action="store_true", help="generate DTB without rkbin/compiler/firmware")
    args = parser.parse_args()
    if args.list_boards:
        for path in sorted(PROFILES.glob("*/board.json")):
            p = load_profile(path)
            print(f"{p['id']}: {p.get('description', '')}")
        return
    if bool(args.board) == bool(args.profile):
        parser.error("provide exactly one of --board or --profile")
    if args.board and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.board):
        parser.error("invalid board id")
    path = args.profile.resolve() if args.profile else PROFILES / args.board / "board.json"
    profile = load_profile(path)
    if args.board and profile["id"] != args.board:
        raise ValueError("board id and profile directory differ")
    mode = args.dtb_mode or profile.get("dtb_mode", "embedded")
    if mode not in ("external", "embedded"):
        raise ValueError("invalid profile DTB mode")
    data_dir = (args.input_dir or ROOT / "board-data/rk3562" / profile["id"]).resolve()
    suffix = "dtb" if args.dtb_only else mode
    output = (args.output or ROOT / "build-rk3562-boards" / profile["id"] / suffix).resolve()
    build(profile, path, data_dir, output, mode, args.dtb_only)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit("RK3562 board build failed: " + str(exc))
