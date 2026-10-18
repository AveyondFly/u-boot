#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0+
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${ROOT_DIR}/scripts/build_rk3562_board.py" "$@"
