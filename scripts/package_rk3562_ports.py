#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Package a verified RK3562 build as an offline ROCKNIX Ports updater."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def screens(output, font):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path, action="append")
    parser.add_argument("--output", default=ROOT / "build-rk3562-ports", type=Path)
    parser.add_argument("--font", type=Path, default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"))
    args = parser.parse_args()
    config = json.loads((ROOT / "tools/ports/uboot/devices.json").read_text())
    builds = []
    selected = set()
    for build_dir in args.build_dir:
        metadata = json.loads((build_dir / "board-build.json").read_text())
        matches = [d for d in config["devices"] if d["id"] == metadata["board_id"]]
        if len(matches) != 1 or metadata["dtb_only"] or metadata["dtb_mode"] != "embedded":
            parser.error("provide an embedded full build with exactly one registered device")
        device = matches[0]
        if device["id"] in selected:
            parser.error("duplicate board build: " + device["id"])
        selected.add(device["id"])
        image = build_dir / "rk3562-uboot-16m.bin"
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest != metadata["artifacts"][image.name] or image.stat().st_size != device["size"]:
            parser.error("build image SHA-256 or size mismatch")
        device.update(sha256=digest, source_commit=metadata["source_commit"])
        builds.append((device, image, build_dir))
    source = ROOT / "tools/ports"
    destination = args.output.resolve()
    for path in [source.resolve(), *(p.resolve() for p in args.build_dir)]:
        if destination == path or destination.is_relative_to(path) or path.is_relative_to(destination):
            parser.error("output must be separate from source/build directories")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    data = destination / "uboot"
    (data / "devices.json").write_text(json.dumps(config, indent=2) + "\n")
    for device, image, build_dir in builds:
        shutil.copy2(image, data / device["image"])
        shutil.copy2(build_dir / "board-build.json", data / (device["id"] + "-board-build.json"))
    screens(data / "screens", args.font)
    (destination / "Update U-Boot.sh").chmod(0o755)
    print(destination)


if __name__ == "__main__":
    main()
