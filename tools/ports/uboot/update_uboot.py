#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Update the running RK3562 boot card from an exact DT model mapping."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

DATA = Path(__file__).resolve().parent
MIN_SCREEN_SECONDS = 3.0
SECTOR = 512


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def choose_device(config, model, compatible):
    if config["schema_version"] != 1:
        raise ValueError("Unsupported device configuration")
    matches = [d for d in config["devices"] if model in d["models"]]
    if len(matches) != 1 or matches[0]["compatible"] not in compatible:
        raise ValueError("No unique compatible image for DT model: " + model)
    device = matches[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", device["id"]):
        raise ValueError("Invalid board ID")
    if device["image"] != device["id"] + "-uboot.bin":
        raise ValueError("Image filename must match the board ID")
    # These entries describe the current RK3562 pre-partition image format.
    if device["offset"] != 64 * SECTOR or device["size"] != 16 * 1024 * 1024 - 64 * SECTOR:
        raise ValueError("Unsupported RK3562 image layout")
    if not re.fullmatch(r"[0-9a-f]{64}", device["sha256"]):
        raise ValueError("Invalid image SHA-256")
    return device


def boot_target(mounts, sys_block=Path("/sys/class/block"), dev_root=Path("/dev")):
    sources = [line.split()[0] for line in mounts.splitlines()
               if len(line.split()) >= 2 and line.split()[1] == "/flash"]
    if len(sources) != 1:
        raise ValueError("Cannot identify the /flash boot partition")
    partition = Path(sources[0]).resolve().name
    match = re.fullmatch(r"(mmcblk[0-9]+)p[0-9]+", partition)
    if not match:
        raise ValueError("Boot partition is not a supported MMC device")
    name = match[1]
    info = sys_block / name
    if int((info / "ro").read_text()) != 0:
        raise ValueError("Boot device is read-only")
    if int((info / "queue/logical_block_size").read_text()) != SECTOR:
        raise ValueError("Unsupported boot device sector size")
    starts = [int((p / "start").read_text()) for p in sys_block.glob(name + "p*")
              if re.fullmatch(re.escape(name) + r"p[0-9]+", p.name)]
    if not starts:
        raise ValueError("Cannot determine the first partition boundary")
    return dev_root / name, min(starts) * SECTOR


def make_plan():
    if os.geteuid() != 0:
        raise ValueError("Run this port as root")
    tree = Path("/sys/firmware/devicetree/base")
    model = (tree / "model").read_bytes().rstrip(b"\0").decode().strip()
    compatible = (tree / "compatible").read_bytes().decode().strip("\0").split("\0")
    device = choose_device(json.loads((DATA / "devices.json").read_text()), model, compatible)
    image = DATA / device["image"]
    if image.is_symlink() or image.stat().st_size != device["size"] or digest(image) != device["sha256"]:
        raise ValueError("Image size or SHA-256 verification failed")
    with image.open("rb") as source:
        if source.read(4) != b"RKNS":
            raise ValueError("Missing RKNS IDB header")
        source.seek(16320 * SECTOR)
        if source.read(4) != bytes.fromhex("d00dfeed"):
            raise ValueError("Missing U-Boot FIT header")
    target, first_partition = boot_target(Path("/proc/mounts").read_text())
    if not stat.S_ISBLK(target.stat().st_mode):
        raise ValueError("Boot target is not a block device")
    end = device["offset"] + device["size"]
    if first_partition < end:
        raise ValueError("Image would overlap the first partition")
    size = int(subprocess.check_output(["blockdev", "--getsize64", str(target)], text=True))
    if size < end or shutil.disk_usage(DATA).free < end * 2 + 1024 * 1024:
        raise ValueError("Insufficient device size or space for the backup")
    return dict(device, model=model, image=str(image), target=str(target),
                first_partition=first_partition)


def copy_exact(source, destination, size):
    remaining = size
    while remaining:
        block = source.read(min(1024 * 1024, remaining))
        if not block:
            raise ValueError("Short read while saving the boot backup")
        destination.write(block)
        remaining -= len(block)
    destination.flush()
    os.fsync(destination.fileno())


def flash(plan, run_dir):
    """Save a complete front-of-disk backup, dd the image, then read it back."""
    run_dir.mkdir(parents=True)
    end = plan["offset"] + plan["size"]
    backup = run_dir / "boot-front-before.bin"
    with open(plan["target"], "rb", buffering=0) as source, backup.open("xb") as output:
        copy_exact(source, output, end)
    (run_dir / "before.json").write_text(json.dumps(
        dict(plan, backup_sha256=digest(backup)), indent=2) + "\n")
    # Stage and recheck the exact bytes dd will use before touching the card.
    with tempfile.TemporaryDirectory(prefix=".image-", dir=run_dir) as temporary:
        staged = Path(temporary) / "uboot.bin"
        shutil.copyfile(plan["image"], staged)
        if staged.stat().st_size != plan["size"] or digest(staged) != plan["sha256"]:
            raise ValueError("Image changed after validation; boot card was not written")
        with (run_dir / "dd.log").open("wb") as log:
            subprocess.run(["dd", "if=" + str(staged), "of=" + plan["target"],
                            "bs=512", "seek=" + str(plan["offset"] // SECTOR),
                            "conv=notrunc,fsync", "status=none"],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        # Direct reads bypass the block cache after dd's fsync.
        with (run_dir / "verify-dd.log").open("wb") as log:
            readback = subprocess.run([
                "dd", "if=" + plan["target"], "bs=512",
                "skip=" + str(plan["offset"] // SECTOR),
                "count=" + str(plan["size"] // SECTOR),
                "iflag=direct,fullblock", "status=none"],
                stdout=subprocess.PIPE, stderr=log, check=True).stdout
        if len(readback) != plan["size"] or hashlib.sha256(readback).hexdigest() != plan["sha256"]:
            raise ValueError("Boot card read-back SHA-256 verification failed; retain the backup")
    return backup


class Display:
    def __init__(self, work, log):
        self.work, self.log = work, log
        self.process = self.connection = self.reader = None
        self.shown_at = None
        self.history = []

    def start(self):
        environment = os.environ.copy()
        if not environment.get("WAYLAND_DISPLAY"):
            for runtime in (Path("/var/run/0-runtime-dir"), Path("/run/user/0")):
                sockets = sorted(p for p in runtime.glob("wayland-*") if p.is_socket())
                if sockets:
                    environment.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY=sockets[0].name)
                    break
        socket_path = str(self.work / "mpv.sock")
        self.process = subprocess.Popen([
            "mpv", "--no-config", "--fullscreen", "--no-audio", "--no-osc",
            "--input-default-bindings=no", "--input-vo-keyboard=no", "--input-terminal=no",
            "--cursor-autohide=always", "--idle=yes", "--force-window=immediate",
            "--keep-open=yes", "--image-display-duration=inf",
            "--input-ipc-server=" + socket_path], env=environment,
            stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("mpv exited before opening the update screen")
            try:
                connection = socket.socket(socket.AF_UNIX)
                connection.connect(socket_path)
                self.connection = connection
                self.reader = connection.makefile("r")
                connection.settimeout(15)
                return
            except OSError:
                connection.close()
                time.sleep(0.1)
        raise RuntimeError("mpv IPC did not become ready")

    def send(self, command):
        self.connection.sendall((json.dumps({"command": command}) + "\n").encode())

    def show(self, state, preview=False):
        self.hold()
        self.send(["loadfile", str(DATA / "screens" / (state + ".png")), "replace"])
        deadline = time.monotonic() + 15
        loaded = False
        while time.monotonic() < deadline:
            line = self.reader.readline()
            if not line:
                raise RuntimeError("mpv closed while loading the status screen")
            event = json.loads(line)
            if event.get("event") == "file-loaded":
                loaded = True
            if loaded and event.get("event") == "playback-restart":
                self.shown_at = time.monotonic()
                self.history.append({"state": state, "shown_at": datetime.now(timezone.utc).isoformat()})
                if preview:
                    self.send(["show-text", "PREVIEW ONLY - NO DISK WRITE", 100000])
                return
        raise RuntimeError("mpv did not display the status screen")

    def hold(self):
        if self.shown_at is not None:
            time.sleep(max(0, self.shown_at + MIN_SCREEN_SECONDS - time.monotonic()))
            self.history[-1]["display_seconds"] = round(time.monotonic() - self.shown_at, 3)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.reader:
                self.reader.close()
            if self.connection:
                self.connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate model, image and target; never write")
    mode.add_argument("--preview", choices=("success", "failure"), help="show both screens; never write")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(make_plan(), indent=2))
        return 0
    DATA.joinpath("logs").mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    result = {"started_at": stamp, "preview": args.preview, "success": False, "write_verified": False}
    with open("/tmp/uboot-ports-update.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another U-Boot updater is running", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory(prefix="uboot-screen-") as temporary, \
                (DATA / "logs" / (stamp + "-mpv.log")).open("wb") as log:
            display = Display(Path(temporary), log)
            code = 1
            try:
                display.start()
                display.show("updating", preview=bool(args.preview))
                if args.preview:
                    display.show(args.preview, preview=True)
                    code = 0
                else:
                    plan = make_plan()
                    result["plan"] = plan
                    display.hold()
                    if display.process.poll() is not None:
                        raise RuntimeError("Update screen closed; boot card was not written")
                    backup = flash(plan, DATA / "backups" / stamp)
                    result.update(success=True, write_verified=True, backup=str(backup))
                    print("U-Boot update succeeded; read-back verified. Backup:", backup)
                    display.show("success")
                    code = 0
            except Exception as error:
                result["error"] = str(error)
                print("U-Boot update failed:", error, file=sys.stderr)
                try:
                    if display.connection:
                        display.show("failure", preview=bool(args.preview))
                except Exception as screen_error:
                    result["screen_error"] = str(screen_error)
            finally:
                display.hold()
                display.close()
                result["screens"] = display.history
                result["finished_at"] = datetime.now(timezone.utc).isoformat()
                (DATA / "logs" / (stamp + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
            return code


if __name__ == "__main__":
    sys.exit(main())
