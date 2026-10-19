#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Compare board MMC wiring, including referenced pins and power providers."""

import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from extract_rockchip_rkns import extract as extract_idb
from rk3562_control_dtb import read_tree


MMC_PATHS = ("/mmc@ff870000", "/mmc@ff880000", "/mmc@ff890000")
BOOT_MMC = MMC_PATHS[:2]


def canonical(path):
    # The vendor boot and Linux trees name the same pin group differently.
    return path.replace("/sdmmc0-pins/", "/sdmmc0/")


def mmc_configuration(dtb, controllers=None, removed=()):
    tree = read_tree(dtb)
    handles = {int.from_bytes(props["phandle"], "big"): name
               for name, props in tree.items() if "phandle" in props}
    roots = tuple(controllers) if controllers is not None else tuple(
        name for name in MMC_PATHS if name in tree)
    pending, result = list(roots), {}

    def references(value, cells=None):
        if len(value) % 4:
            raise ValueError("invalid MMC reference data")
        words = [int.from_bytes(value[i:i + 4], "big") for i in range(0, len(value), 4)]
        refs = []
        while words:
            handle = words.pop(0)
            if handle not in handles:
                raise ValueError("unresolved MMC reference: " + hex(handle))
            target = handles[handle]
            count = int.from_bytes(tree[target][cells], "big") if cells else 0
            if len(words) < count:
                raise ValueError("incomplete MMC reference: " + target)
            refs.append([canonical(target), *words[:count]])
            del words[:count]
            pending.append(target)
        return refs

    while pending:
        path = pending.pop()
        if path in result:
            continue
        if path not in tree:
            raise ValueError("missing MMC node: " + path)
        values = result[path] = {}
        for key, value in tree[path].items():
            if (key in ("phandle", "linux,phandle") or key.startswith("u-boot,")
                    or key in removed):
                continue
            cell_property = {"clocks": "#clock-cells", "assigned-clocks": "#clock-cells",
                             "assigned-clock-parents": "#clock-cells", "resets": "#reset-cells",
                             "power-domains": "#power-domain-cells", "phys": "#phy-cells",
                             "dmas": "#dma-cells", "pwms": "#pwm-cells"}.get(key)
            if cell_property:
                values[key] = references(value, cell_property)
            elif key in ("gpio", "gpios") or key.endswith("-gpios"):
                values[key] = references(value, "#gpio-cells")
            elif key.endswith("-supply") or key in ("mmc-pwrseq", "interrupt-parent", "rockchip,grf") or re.fullmatch(r"pinctrl-[0-9]+", key):
                values[key] = references(value)
            elif key == "rockchip,pins":
                if len(value) % 16:
                    raise ValueError("invalid MMC Rockchip pin data")
                pins = []
                for i in range(0, len(value), 16):
                    pin = [int.from_bytes(value[j:j + 4], "big") for j in range(i, i + 12, 4)]
                    pin.append(references(value[i + 12:i + 16])[0][0])
                    pins.append(pin)
                values[key] = pins
            else:
                values[key] = value.hex()
        # SDIO function nodes and regulator suspend settings are board data too.
        if path in roots or "regulator-name" in tree[path]:
            pending.extend(name for name in tree if name.rsplit("/", 1)[0] == path)
    return {canonical(name): props for name, props in result.items()}


def verify_mmc_configuration(reference, candidate, controllers=None, removed=()):
    expected = mmc_configuration(reference, controllers, removed)
    actual = mmc_configuration(candidate, controllers, removed)
    if actual != expected:
        different = [name + ":" + key
                     for name in sorted(expected.keys() | actual.keys())
                     for key in sorted(expected.get(name, {}).keys() | actual.get(name, {}).keys())
                     if expected.get(name, {}).get(key) != actual.get(name, {}).get(key)]
        raise ValueError("board MMC configuration changed: " + ", ".join(different))
    return {"controllers": list(controllers) if controllers is not None else
            [name for name in MMC_PATHS if name in read_tree(reference)],
            "nodes": len(expected),
            "sha256": hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()}


def extract_vendor_trees(idb, image, output):
    """Get authenticated SPL and U-Boot control DTBs from the board inputs."""
    output.mkdir(parents=True, exist_ok=True)
    spl = output / "vendor-spl.bin"
    extract_idb(idb, spl, 1)
    blob = spl.read_bytes()
    candidates, position = [], 0
    while True:
        offset = blob.find(bytes.fromhex("d00dfeed"), position)
        if offset < 0:
            break
        position = offset + 4
        if offset + 40 > len(blob):
            continue
        size = struct.unpack_from(">I", blob, offset + 4)[0]
        if not 40 <= size <= len(blob) - offset:
            continue
        candidate = output / "vendor-spl-control.dtb"
        candidate.write_bytes(blob[offset:offset + size])
        try:
            tree = read_tree(candidate)
        except (ValueError, IndexError, struct.error, UnicodeDecodeError):
            continue
        if all(path in tree for path in BOOT_MMC):
            candidates.append(blob[offset:offset + size])
    if len(candidates) != 1:
        raise ValueError("expected one SPL control DTB with eMMC and TF1")
    spl_dtb = output / "vendor-spl-control.dtb"
    spl_dtb.write_bytes(candidates[0])
    fit, blob = read_tree(image), image.read_bytes()
    data = fit["/images/fdt"]
    if data.get("compression") != b"none\0":
        raise ValueError("compressed vendor control DTB is unsupported")
    offset = int.from_bytes(data["data-position"], "big")
    size = int.from_bytes(data["data-size"], "big")
    if offset < struct.unpack_from(">I", blob, 4)[0] or size <= 0 or offset + size > len(blob):
        raise ValueError("vendor control DTB is outside the FIT")
    payload = blob[offset:offset + size]
    checksum = fit["/images/fdt/hash"]
    if checksum["algo"] != b"sha256\0" or hashlib.sha256(payload).digest() != checksum["value"]:
        raise ValueError("vendor control DTB FIT hash mismatch")
    uboot_dtb = output / "vendor-uboot-control.dtb"
    uboot_dtb.write_bytes(payload)
    for path in (spl_dtb, uboot_dtb):
        tree = read_tree(path)
        if any(tree.get(node, {}).get("status") != b"okay\0" for node in BOOT_MMC):
            raise ValueError("vendor boot storage is missing or disabled")
        if MMC_PATHS[2] in tree:
            raise ValueError("vendor early SDIO policy requires explicit review")
    return {"spl": spl_dtb, "uboot": uboot_dtb}


def verify_vendor_references(control, references):
    expected = control.get("mmc_reference_sha256", {})
    if set(expected) != {"spl", "uboot"}:
        raise ValueError("board profile must pin its vendor SPL/U-Boot reference DTBs")
    for stage, path in references.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[stage]:
            raise ValueError("vendor early DTB checksum mismatch: " + stage +
                             "; use this board's original eMMC input package")


def write_mmc_include(base, reference, output):
    """Translate vendor boot phandles into the board's generated source tree."""
    tree, vendor = read_tree(base), read_tree(reference)
    handles = {int.from_bytes(v["phandle"], "big"): n
               for n, v in vendor.items() if "phandle" in v}
    targets = {canonical(n): n for n in tree}

    def target(path):
        if canonical(path) not in targets:
            raise ValueError("missing vendor MMC provider in board DTB: " + path)
        return targets[canonical(path)]

    def remap(value, cell_property=None):
        words = [int.from_bytes(value[i:i + 4], "big") for i in range(0, len(value), 4)]
        result = []
        while words:
            source = handles[words.pop(0)]
            props = tree[target(source)]
            result.append(int.from_bytes(props["phandle"], "big"))
            count = int.from_bytes(vendor[source][cell_property], "big") if cell_property else 0
            result.extend(words[:count])
            del words[:count]
        return b"".join(struct.pack(">I", word) for word in result)

    lines = ["// Generated from the authenticated vendor U-Boot control DTB."]
    # Pinctrl wiring and drive strength are compared along with the controllers.
    configuration = mmc_configuration(reference, BOOT_MMC)
    related = [n for n in vendor if canonical(n) in configuration
               and n.startswith("/pinctrl/")]
    for source in [*BOOT_MMC, *related]:
        destination = target(source)
        lines.append(f"&{{{destination}}} {{")
        keys = set(tree[destination])
        if source in BOOT_MMC:
            # rk3562-u-boot.dtsi also supplies EVB defaults before this include.
            keys.update(("mmc-hs400-1_8v", "mmc-hs400-enhanced-strobe",
                         "fixed-emmc-driver-type", "bus-width", "pinctrl-0", "pinctrl-names"))
        for key in sorted(keys - vendor[source].keys()):
            if key not in ("phandle", "linux,phandle") and not key.startswith("u-boot,"):
                lines.append(f"\t/delete-property/ {key};")
        for key, value in vendor[source].items():
            if key in ("phandle", "linux,phandle") or key.startswith("u-boot,"):
                continue
            if key == "clocks":
                value = remap(value, "#clock-cells")
            elif key == "resets":
                value = remap(value, "#reset-cells")
            elif re.fullmatch(r"pinctrl-[0-9]+", key):
                value = remap(value)
            elif key == "rockchip,pins":
                value = b"".join(value[i:i + 12] + remap(value[i + 12:i + 16])
                                 for i in range(0, len(value), 16))
            elif key.endswith("-supply") or key in ("mmc-pwrseq", "interrupt-parent"):
                raise ValueError("vendor early MMC power dependencies require explicit support")
            lines.append("\t" + key + (" = [" + value.hex(" ") + "]" if value else "") + ";")
        lines.append("};")
    output.write_text("\n".join(lines) + "\n")


def write_spl_overlay(uboot_reference, spl_reference, output):
    """Preserve stage differences; never silently reuse U-Boot's MMC modes."""
    uboot, spl = read_tree(uboot_reference), read_tree(spl_reference)
    lines = ["/dts-v1/; /plugin/;", "/ {"]
    # Current vendor stages differ only by eMMC HS400 capabilities. Reject
    # other changes until their phandles/deletion semantics are supported.
    allowed = {"mmc-hs400-1_8v", "mmc-hs400-enhanced-strobe"}
    for index, node in enumerate(BOOT_MMC):
        differences = {key for key in uboot[node].keys() | spl[node].keys()
                       if uboot[node].get(key) != spl[node].get(key)}
        if differences - allowed or any(spl[node].get(key) != b"" for key in differences):
            raise ValueError("unsupported vendor SPL/U-Boot MMC stage differences")
        lines += [f"fragment@{index} {{ target-path = \"{node}\"; __overlay__ {{"]
        lines.extend(key + ";" for key in sorted(differences))
        lines += ["}; };"]
    lines += ["};"]
    source = output.with_suffix(".dtso")
    source.write_text("\n".join(lines) + "\n")
    subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(output), str(source)], check=True)


def validate_boot_storage(reference, artifacts):
    report = {"reference": "authenticated vendor IDB/FIT control DTBs", "stages": {}}
    for stage, name in (("spl", "u-boot-spl.dtb"), ("uboot", "u-boot.dtb")):
        candidate = artifacts / name
        tree = read_tree(candidate)
        if set(n for n in MMC_PATHS if n in tree) != set(BOOT_MMC):
            raise ValueError("early boot must retain exactly eMMC and TF1")
        info = verify_mmc_configuration(reference[stage], candidate, BOOT_MMC)
        expected = b"/mmc@ff880000\0/mmc@ff870000\0"
        order = tree.get("/chosen", {}).get("u-boot,spl-boot-order", b"")
        if not order.startswith(expected):
            raise ValueError("boot order must prefer TF1 before eMMC")
        if tree.get("/aliases", {}).get("mmc0") != b"/mmc@ff870000\0" or tree.get("/aliases", {}).get("mmc1") != b"/mmc@ff880000\0":
            raise ValueError("boot MMC aliases are inconsistent")
        info.update(reference_sha256=hashlib.sha256(reference[stage].read_bytes()).hexdigest(),
                    tf1_before_emmc=True)
        report["stages"][stage] = info
    return report


def verify_packed_spl(artifacts):
    """Check the actual boot payload, rather than only the exported DTB."""
    packed = artifacts / "packed-spl.bin"
    extract_idb(artifacts / "idblock.img", packed, 1)
    actual = packed.read_bytes()
    expected = (artifacts / "u-boot-spl.bin").read_bytes()
    dtb = (artifacts / "u-boot-spl.dtb").read_bytes()
    if actual[:len(expected)] != expected or any(actual[len(expected):]):
        raise ValueError("IDB contains a different SPL than this board build")
    if not expected.endswith(dtb):
        raise ValueError("packed SPL does not end with this board's control DTB")
    return {"compiled_sha256": hashlib.sha256(expected).hexdigest(),
            "packed_sha256": hashlib.sha256(actual).hexdigest(),
            "padding_bytes": len(actual) - len(expected), "control_dtb_verified": True}
