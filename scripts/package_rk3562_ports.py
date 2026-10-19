#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Package a verified RK3562 build as an offline ROCKNIX Ports updater."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

from build_rk3562_board import PROFILES, input_file

ROOT = Path(__file__).resolve().parents[1]


def screens(output, font):
    from PIL import Image, ImageDraw, ImageFont

    output.mkdir(parents=True, exist_ok=True)
    large = ImageFont.truetype(str(font), 66, index=2)
    small = ImageFont.truetype(str(font), 32, index=2)
    label = ImageFont.truetype(str(font), 23, index=2)
    states = {
        "updating": ("#efba49", "正在更新 U-Boot", "请勿关机、重启或拔出存储卡", "正在备份、写入并校验启动固件"),
        "success": ("#68d9a4", "U-Boot 更新成功", "固件写入及读回校验已完成", "重启设备后生效 · 原固件备份已保存"),
        "failure": ("#f47979", "U-Boot 更新失败", "请查看 uboot/logs 中的错误记录", "如已开始写入，请保留 uboot/backups 中的备份"),
    }
    for name, (color, title, subtitle, detail) in states.items():
        image = Image.new("RGB", (1280, 720), "#101b2b")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((70, 75, 1210, 645), radius=30, fill="#17273d", outline="#2c405b", width=2)
        draw.rounded_rectangle((110, 122, 1170, 130), radius=4, fill=color)
        for y, text, face, ink in (
            (194, "BOOTLOADER UPDATE", label, "#8da2bc"),
            (304, title, large, color), (416, subtitle, small, "#f1f5fb"),
            (478, detail, label, "#a8b8cc"), (592, "U-Boot · ROCKNIX", label, "#7288a4"),
        ):
            draw.text((640, y), text, font=face, fill=ink, anchor="mm")
        image.save(output / (name + ".png"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_ports(build_dirs, destination, font, expected_boards=None,
                require_all=False, source_commit=None, require_clean=False):
    config = json.loads((ROOT / "tools/ports/uboot/devices.json").read_text())
    if config.get("schema_version") != 1:
        raise ValueError("unsupported Ports device configuration")
    profiles = {path.parent.name for path in PROFILES.glob("*/board.json")}
    registered = [device["id"] for device in config["devices"]]
    if len(registered) != len(set(registered)):
        raise ValueError("duplicate device registration")
    if require_all and set(registered) != profiles:
        raise ValueError("Ports device registrations must match all board profiles")
    builds, selected, models, revisions = [], set(), set(), set()
    for build_dir in build_dirs:
        metadata = json.loads((build_dir / "board-build.json").read_text())
        matches = [d for d in config["devices"] if d["id"] == metadata["board_id"]]
        if len(matches) != 1 or metadata["dtb_only"] or metadata["dtb_mode"] != "embedded":
            raise ValueError("provide an embedded full build with exactly one registered device")
        device = matches[0]
        if device["id"] not in profiles or device["image"] != device["id"] + "-uboot.bin":
            raise ValueError("invalid board registration or image filename")
        if device["id"] in selected:
            raise ValueError("duplicate board build: " + device["id"])
        selected.add(device["id"])
        if not device["models"] or len(set(device["models"])) != len(device["models"]) or models.intersection(device["models"]):
            raise ValueError("device models must be nonempty and unique")
        models.update(device["models"])
        commit, diff = metadata["source_commit"], metadata["tracked_source_diff_sha256"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", diff):
            raise ValueError("invalid source revision")
        if source_commit and commit != source_commit:
            raise ValueError("build source commit differs from the requested revision")
        if require_clean and diff != hashlib.sha256(b"").hexdigest():
            raise ValueError("CI Ports bundle requires builds from a clean source tree")
        revisions.add((commit, diff))
        payloads = ("rk3562-uboot-16m.bin", "board-mmc.json", "board-power.json")
        for name in payloads:
            if name not in metadata["artifacts"] or sha256(input_file(build_dir, name)) != metadata["artifacts"][name]:
                raise ValueError("build artifact checksum mismatch: " + name)
        mmc = json.loads((build_dir / "board-mmc.json").read_text())
        if (mmc["packed_spl"].get("control_dtb_verified") is not True or
                any(mmc["stages"][stage].get("tf1_before_emmc") is not True for stage in ("spl", "uboot"))):
            raise ValueError("boot storage and packed SPL checks must pass")
        image = build_dir / payloads[0]
        digest = metadata["artifacts"][image.name]
        if (image.stat().st_size != device["size"] or device["size"] != 16 * 1024 * 1024 - 32768
                or device["offset"] != 32768):
            raise ValueError("unsupported Ports image size or layout")
        device.update(sha256=digest, source_commit=metadata["source_commit"])
        builds.append((device, image, build_dir))
    if not builds or len(revisions) != 1:
        raise ValueError("provide board builds from exactly one source revision")
    if expected_boards is not None and selected != set(expected_boards):
        raise ValueError("Ports builds do not match the requested board matrix")
    if require_all and selected != profiles:
        raise ValueError("Ports release must contain all board profiles")
    config["devices"] = [record[0] for record in sorted(builds, key=lambda record: record[0]["id"])]
    source = ROOT / "tools/ports"
    destination = destination.resolve()
    for path in [source.resolve(), *(p.resolve() for p in build_dirs)]:
        if destination == path or destination.is_relative_to(path) or path.is_relative_to(destination):
            raise ValueError("output must be separate from source/build directories")
    if destination.exists() and any(destination.iterdir()) and not (destination / "uboot/package.json").is_file():
        raise ValueError("output is not an owned Ports build directory; choose a new --output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rk3562-ports-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "ports"
        data = stage / "uboot"
        data.mkdir(parents=True)
        shutil.copy2(source / "Update U-Boot.sh", stage)
        for name in ("update_uboot.py", "README.txt"):
            shutil.copy2(source / "uboot" / name, data / name)
        (data / "devices.json").write_text(json.dumps(config, indent=2) + "\n")
        for device, image, build_dir in builds:
            shutil.copy2(image, data / device["image"])
            for name in ("board-build.json", "board-mmc.json", "board-power.json"):
                shutil.copy2(build_dir / name, data / (device["id"] + "-" + name))
        commit, diff = next(iter(revisions))
        package = dict(schema_version=1, boards=sorted(selected), source_commit=commit,
                       tracked_source_diff_sha256=diff,
                       contains_all_profiles=selected == profiles)
        (data / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        screens(data / "screens", font)
        (stage / "Update U-Boot.sh").chmod(0o755)
        sums = "".join(f"{sha256(path)}  {path.relative_to(stage).as_posix()}\n"
                       for path in sorted(stage.rglob("*")) if path.is_file())
        (data / "SHA256SUMS").write_text(sums)
        previous = Path(tmp) / "previous"
        if destination.exists():
            destination.rename(previous)
        try:
            stage.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    return package


def archive_ports(directory, output):
    package = json.loads((directory / "uboot/package.json").read_text())
    output = output.resolve()
    if output == directory.resolve() or output.is_relative_to(directory.resolve()):
        raise ValueError("archive output must be outside the Ports directory")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("rk3562-update-uboot-ports-" + package["source_commit"][:12] + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(directory).as_posix())
    Path(str(archive) + ".sha256").write_text(f"{sha256(archive)}  {archive.name}\n")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--build-dir", type=Path, action="append")
    inputs.add_argument("--build-root", type=Path, help="directory of downloaded per-board build artifacts")
    parser.add_argument("--output", default=ROOT / "build-rk3562-ports", type=Path)
    parser.add_argument("--archive-dir", type=Path, help="also produce a ZIP that extracts directly into ports/")
    parser.add_argument("--expected-boards", help="exact board IDs expected from the workflow matrix, comma separated")
    parser.add_argument("--require-all", action="store_true", help="require every registered board profile")
    parser.add_argument("--source-commit", help="require this source commit")
    parser.add_argument("--require-clean", action="store_true", help="reject builds with tracked source changes")
    parser.add_argument("--font", type=Path, default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"))
    args = parser.parse_args()
    builds = (args.build_dir if args.build_dir else
              [path.parent for path in sorted(args.build_root.glob("*/board-build.json"))])
    expected = args.expected_boards.split(",") if args.expected_boards is not None else None
    build_ports(builds, args.output, args.font, expected, args.require_all, args.source_commit, args.require_clean)
    print(args.output.resolve())
    if args.archive_dir:
        print(archive_ports(args.output, args.archive_dir))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        sys.exit("RK3562 Ports package failed: " + str(error))
