#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0+
# Compatibility entry point. New boards should use build_uboot_rk3562_board.sh.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${ROOT_DIR}/build_uboot_rk3562_board.sh" --board ngp45h "$@"
