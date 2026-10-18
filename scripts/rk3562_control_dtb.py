#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Preserve board DTB data when preparing U-Boot's early control tree."""

import hashlib
import json
from pathlib import Path
import re
import struct


def read_tree(path):
    data = Path(path).read_bytes()
    magic, total, position, strings = struct.unpack_from(">4I", data)
    if magic != 0xd00dfeed or total > len(data):
        raise ValueError("invalid device tree: " + str(path))
    stack, nodes = [], {}
    while position < total:
        token = struct.unpack_from(">I", data, position)[0]
        position += 4
        if token == 1:
            end = data.index(b"\0", position)
            stack.append(data[position:end].decode())
            nodes["/" + "/".join(stack[1:])] = {}
            position = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, offset = struct.unpack_from(">II", data, position)
            position += 8
            start = strings + offset
            name = data[start:data.index(b"\0", start)].decode()
            nodes["/" + "/".join(stack[1:])][name] = data[position:position + size]
            position = (position + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            return nodes
        else:
            raise ValueError("invalid device tree token")
    raise ValueError("incomplete device tree")


def vendor_dtsi(dtb, output):
    """Restore the vendor's symbols as DTS labels without copying EVB data."""
    nodes = read_tree(dtb)
    labels = {}
    symbols = nodes.get("/__symbols__", {})
    if not symbols:
        raise ValueError("board DTB needs __symbols__ for the early control DTS")
    for label, value in symbols.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", label):
            raise ValueError("invalid board DT symbol: " + label)
        path = value.rstrip(b"\0").decode()
        if path not in nodes:
            raise ValueError("board DT symbol points to a missing node: " + label)
        labels.setdefault(path, []).append(label)
    # Linux does not label these containers; rk3562-u-boot.dtsi uses them.
    fallback = {"firmware": "/firmware", "psci": "/psci"}
    bus4 = symbols.get("sdmmc0_bus4")
    if bus4:
        fallback["sdmmc0_pins"] = bus4.rstrip(b"\0").decode().rsplit("/", 1)[0]
    for label, path in fallback.items():
        if label not in symbols:
            if path not in nodes:
                raise ValueError("missing RK3562 early device: " + path)
            labels.setdefault(path, []).append(label)
    children = {path: [] for path in nodes}
    for path in nodes:
        if path != "/":
            children[path.rsplit("/", 1)[0] or "/"].append(path)
    lines = ["// Generated from this board's verified charging DTB; do not edit."]

    def encode(value):
        if not value:
            return ""
        try:
            strings = value[:-1].decode().split("\0") if value.endswith(b"\0") else []
        except UnicodeDecodeError:
            strings = []
        if strings and all(item and all(char.isprintable() for char in item) for item in strings):
            return " = " + ", ".join(json.dumps(item, ensure_ascii=False) for item in strings)
        if len(value) % 4 == 0:
            cells = ["0x" + value[i:i + 4].hex() for i in range(0, len(value), 4)]
            return " = <" + " ".join(cells) + ">"
        return " = [" + value.hex(" ") + "]"

    def emit(path, depth):
        if path in ("/__symbols__", "/__fixups__", "/__local_fixups__"):
            return
        prefix = "\t" * depth
        name = "/" if path == "/" else path.rsplit("/", 1)[1]
        label = "".join(item + ": " for item in labels.get(path, []))
        lines.append(prefix + label + name + " {")
        for key, value in nodes[path].items():
            # Keep the exact bytes and existing phandles. Restore strings and
            # cells for a readable DTS; unusual byte data stays a byte array.
            lines.append(prefix + "\t" + key + encode(value) + ";")
        for child in children[path]:
            emit(child, depth + 1)
        lines.append(prefix + "};")

    emit("/", 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")


def power_configuration(path):
    """Compare voltages, enable flags and supply wiring by node paths."""
    nodes = read_tree(path)
    phandles = {int.from_bytes(props["phandle"], "big"): name
                for name, props in nodes.items() if "phandle" in props}

    def references(value, cell_property=None):
        cells = [int.from_bytes(value[i:i + 4], "big") for i in range(0, len(value), 4)]
        result = []
        while cells:
            handle = cells.pop(0)
            if handle not in phandles:
                raise ValueError("unresolved power supply/GPIO phandle")
            target = phandles[handle]
            count = int.from_bytes(nodes[target][cell_property], "big") if cell_property else 0
            result.append([target, *cells[:count]])
            del cells[:count]
        return result

    result = {}
    power_nodes = {name for name, props in nodes.items() if "regulator-name" in props
                   or b"io-voltage-domain" in props.get("compatible", b"")}
    pmics = {name for name, props in nodes.items()
             if b"rockchip,rk817" in props.get("compatible", b"").split(b"\0")}
    for name, props in nodes.items():
        if name not in pmics and not any(name == root or name.startswith(root + "/") for root in power_nodes):
            continue
        values = {}
        for key, value in props.items():
            if key in ("phandle", "linux,phandle") or key.startswith("u-boot,"):
                continue
            if key.endswith("-supply") or key == "interrupt-parent" or re.fullmatch(r"pinctrl-[0-9]+", key):
                values[key] = references(value)
            elif key == "gpio" or key == "gpios" or key.endswith("-gpios"):
                values[key] = references(value, "#gpio-cells")
            elif key == "pwms":
                values[key] = references(value, "#pwm-cells")
            else:
                values[key] = value.hex()
        result[name] = values
    return result


def verify_power_configuration(reference, *candidates):
    expected = power_configuration(reference)
    for path in candidates:
        actual = power_configuration(path)
        if actual != expected:
            different = sorted(name for name in expected.keys() | actual.keys()
                               if expected.get(name) != actual.get(name))
            raise ValueError("board power configuration changed: " + ", ".join(different))
    encoded = json.dumps(expected, sort_keys=True).encode()
    return {"nodes": len(expected), "sha256": hashlib.sha256(encoded).hexdigest()}
