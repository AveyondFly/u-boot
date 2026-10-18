#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0+
set -euo pipefail

destination="${1:?Usage: bash scripts/ci_rk3562_toolchain.sh CACHE_DIRECTORY}"
package=gcc-linaro-6.3.1-2017.05-x86_64_aarch64-linux-gnu
digest=26b45fdfb1656f94fa2b5117494f5d338b619c1f453ee3f3e1d84d79e90f2403
original="https://releases.linaro.org/components/toolchain/binaries/6.3-2017.05/aarch64-linux-gnu/$package.tar.xz"
mirror="https://developer.arm.com/-/cdn-downloads/permalink/legacy-linaro-gnu-toolchains/6.3-2017.05/$package.tar.xz"
mkdir -p "$destination"
destination="$(cd "$destination" && pwd)"
archive="$destination/$package.tar.xz"

verify() {
    echo "$digest  $1" | sha256sum --check --status
}

if [[ ! -f "$archive" ]] || ! verify "$archive"; then
    downloaded=false
    for url in "${TOOLCHAIN_URL:-$original}" "$mirror"; do
        if curl --fail --location --retry 3 --connect-timeout 20 --max-time 600 \
            --output "$archive.part" "$url" && verify "$archive.part"; then
            mv "$archive.part" "$archive"
            downloaded=true
            break
        fi
        echo "Download failed or SHA-256 mismatch: $url" >&2
        rm -f "$archive.part"
    done
    if [[ "$downloaded" != true ]]; then
        echo 'Unable to download the pinned Linaro toolchain' >&2
        exit 1
    fi
fi

# Always extract the verified archive, including after a cache hit.
tar -xJf "$archive" -C "$destination"
"$destination/$package/bin/aarch64-linux-gnu-gcc" --version
