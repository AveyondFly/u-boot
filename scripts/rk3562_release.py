#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Shared human-readable RK3562 release identity for CI and offline packages."""

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def release_identity(version=None, release_date=None, ref=None, ref_type=None, soc="rk3562"):
    if soc not in ("rk3562", "rk3566", "rk356x"):
        raise ValueError("unsupported release family")
    tag = None
    if ref_type == "tag":
        match = re.fullmatch(soc + r"-v(" + VERSION + r")(?:-(\d{4}-\d{2}-\d{2}))?", ref or "")
        if not match:
            raise ValueError(f"release tags must be {soc}-vX.Y.Z or {soc}-vX.Y.Z-YYYY-MM-DD")
        tagged_version, tagged_date = match.groups()
        if version and version.removeprefix("v") != tagged_version:
            raise ValueError("release version differs from the tag")
        if release_date and tagged_date and release_date != tagged_date:
            raise ValueError("release date differs from the tag")
        version, release_date, tag = tagged_version, tagged_date or release_date, ref
    version = (version or (ROOT / (soc.upper() + "_VERSION")).read_text().strip()).removeprefix("v")
    if not re.fullmatch(VERSION, version):
        raise ValueError("release version must be X.Y.Z")
    release_date = release_date or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_date):
        raise ValueError("release date must be YYYY-MM-DD")
    date.fromisoformat(release_date)
    return dict(release_version=version, release_date=release_date,
                release_tag=tag or f"{soc}-v{version}-{release_date}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soc", choices=("rk3562", "rk3566", "rk356x"), default="rk3562")
    parser.add_argument("--version")
    parser.add_argument("--date")
    parser.add_argument("--ref")
    parser.add_argument("--ref-type")
    args = parser.parse_args()
    for key, value in release_identity(args.version, args.date, args.ref, args.ref_type, args.soc).items():
        print(key + "=" + value)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit("RK3562 release identity failed: " + str(error))
