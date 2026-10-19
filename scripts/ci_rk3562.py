#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""RK3562 CI matrix, minimal vendor snapshots, and release bundles."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

from build_rk3562_board import PROFILES, ROOT, input_file, load_profile, sha256, verify_snapshot
from rk3562_release import release_identity


def profile_for(board):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", board):
        raise ValueError("invalid board id: " + board)
    profile = load_profile(PROFILES / board / "board.json")
    if profile["id"] != board:
        raise ValueError("board id and profile directory differ")
    return profile


def matrix(boards):
    selected = ([path.parent.name for path in sorted(PROFILES.glob("*/board.json"))]
                if boards == "all" else [name.strip() for name in boards.split(",")])
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("select at least one board, without duplicates")
    for board in selected:
        profile_for(board)
    return {"board": selected}


def input_names(profile):
    inputs = profile["inputs"]
    names = [inputs.get("linux_dtb") or inputs["linux_dts"], inputs["idb"], inputs["uboot"]]
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name or name == "snapshot.json":
            raise ValueError("invalid vendor input name: " + name)
    if len(set(names)) != len(names):
        raise ValueError("vendor input names must be distinct")
    return names


def add_bytes(archive, name, data):
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    entry.mode = 0o644
    archive.addfile(entry, io.BytesIO(data))


def write_checksum(path):
    Path(str(path) + ".sha256").write_text(f"{sha256(path)}  {path.name}\n")


def pack_inputs(board, data_dir, output):
    profile = profile_for(board)
    verify_snapshot(data_dir, board)
    # Include only the build inputs; omit host addresses, diagnostics and backups.
    files = {name: input_file(data_dir, name).read_bytes() for name in input_names(profile)}
    snapshot = {"board_id": board, "sha256": {
        name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["snapshot.json"] = (json.dumps(snapshot, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{board}-inputs.tar.xz"
    with tarfile.open(path, "w:xz") as archive:
        for name, data in files.items():
            add_bytes(archive, name, data)
    write_checksum(path)
    return path


def unpack_inputs(board, path, output):
    profile = profile_for(board)
    checksum = Path(str(path) + ".sha256").read_text().strip().split()
    if (len(checksum) != 2 or checksum[1] != path.name
            or not re.fullmatch(r"[0-9a-f]{64}", checksum[0])
            or sha256(path) != checksum[0]):
        raise ValueError("vendor archive SHA-256 mismatch or invalid checksum file")
    expected = set(input_names(profile)) | {"snapshot.json"}
    files = {}
    with tarfile.open(path, "r:xz") as archive:
        for member in archive:
            # Never extract links or paths chosen by an archive. Only the exact
            # regular files named by this board profile are accepted.
            if not member.isfile() or member.name not in expected or member.name in files:
                raise ValueError("unexpected vendor archive entry: " + member.name)
            files[member.name] = archive.extractfile(member).read()
    if set(files) != expected:
        raise ValueError("vendor archive does not contain all board inputs")
    snapshot = json.loads(files["snapshot.json"])
    if snapshot["board_id"] != board or set(snapshot["sha256"]) != expected - {"snapshot.json"}:
        raise ValueError("vendor snapshot belongs to a different board or has incomplete checksums")
    for name, digest in snapshot["sha256"].items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError("snapshot checksum mismatch: " + name)
    if output.exists() and any(output.iterdir()):
        raise ValueError("vendor output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def package(board, build_dir, output, version=None, release_date=None):
    identity = release_identity(version, release_date)
    profile_for(board)
    metadata = json.loads((build_dir / "board-build.json").read_text())
    if metadata["board_id"] != board or metadata["dtb_only"]:
        raise ValueError("release requires a full build of the selected board")
    mode, commit = metadata["dtb_mode"], metadata["source_commit"]
    if mode not in ("embedded", "external") or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid build mode or source commit")
    artifacts = metadata["artifacts"]
    for required in ("uboot.img", "idblock.img", "rk3562-uboot-16m.bin", "u-boot.config"):
        if required not in artifacts:
            raise ValueError("missing release image/configuration: " + required)
    for name, digest in artifacts.items():
        if sha256(input_file(build_dir, name)) != digest:
            raise ValueError("build artifact checksum mismatch: " + name)
    files = {name: input_file(build_dir, name) for name in [*artifacts, "board-build.json"]}
    sums = "".join(f"{sha256(path)}  {name}\n" for name, path in sorted(files.items()))
    stem = f"rk3562-{board}-{mode}-{identity['release_date']}-v{identity['release_version']}"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (stem + ".tar.xz")
    with tarfile.open(destination, "w:xz") as archive:
        for name, path in sorted(files.items()):
            archive.add(path, arcname=stem + "/" + name, recursive=False)
        add_bytes(archive, stem + "/SHA256SUMS", sums.encode())
    write_checksum(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("matrix")
    select.add_argument("--boards", default="all")
    pack = commands.add_parser("pack-inputs")
    pack.add_argument("--board", required=True)
    pack.add_argument("--input-dir", type=Path)
    pack.add_argument("--output", type=Path, default=ROOT / "build-rk3562-inputs")
    unpack = commands.add_parser("unpack-inputs")
    unpack.add_argument("--board", required=True)
    unpack.add_argument("--archive", type=Path, required=True)
    unpack.add_argument("--output", type=Path, required=True)
    bundle = commands.add_parser("package")
    bundle.add_argument("--board", required=True)
    bundle.add_argument("--build-dir", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--version", help="release version, default: RK3562_VERSION")
    bundle.add_argument("--date", help="release date, default: current date in Asia/Shanghai")
    args = parser.parse_args()
    if args.command == "matrix":
        print("matrix=" + json.dumps(matrix(args.boards), separators=(",", ":")))
    elif args.command == "pack-inputs":
        data = args.input_dir or ROOT / "board-data/rk3562" / args.board
        print(pack_inputs(args.board, data, args.output))
    elif args.command == "unpack-inputs":
        unpack_inputs(args.board, args.archive, args.output)
    else:
        print(package(args.board, args.build_dir, args.output, args.version, args.date))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, tarfile.TarError) as exc:
        sys.exit("RK3562 CI failed: " + str(exc))
