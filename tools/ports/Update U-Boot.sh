#!/bin/bash
# SPDX-License-Identifier: GPL-2.0+
set -euo pipefail
PORT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$PORT_DIR/uboot/update_uboot.py" "$@"
