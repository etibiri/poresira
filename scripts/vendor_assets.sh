#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Download the front-end libraries into static/vendor so the app never needs a CDN at runtime.
# Checksums are pinned; after bumping a version, run with SKIP to print the new ones.
set -eu

BOOTSTRAP_VERSION=5.3.9
HTMX_VERSION=2.0.11
ALPINE_VERSION=3.17.5
CDN=https://cdn.jsdelivr.net/npm

target="$(dirname "$0")/../static/vendor"
mkdir -p "$target/licenses"

fetch() {
    url="$1"; destination="$2"; expected_sha256="$3"
    curl -fsSL --retry 5 --retry-all-errors "$url" -o "$destination"
    # Drop source-map references: the maps are not shipped and break manifest static storage.
    sed -i '/^\/\*# sourceMappingURL=.*\*\/$/d; s#//\# sourceMappingURL=[^ ]*$##' "$destination"
    actual_sha256="$(sha256sum "$destination" | cut -d' ' -f1)"
    if [ "$expected_sha256" != "SKIP" ] && [ "$actual_sha256" != "$expected_sha256" ]; then
        echo "Checksum mismatch for $destination: $actual_sha256" >&2
        exit 1
    fi
    echo "$actual_sha256  $destination"
}

fetch "$CDN/bootstrap@$BOOTSTRAP_VERSION/dist/css/bootstrap.min.css" "$target/bootstrap.min.css" 06395d93f5adbc0593ed84544eb043fa32ab8619c6b7195e803e119f5b9f3672
fetch "$CDN/bootstrap@$BOOTSTRAP_VERSION/dist/js/bootstrap.bundle.min.js" "$target/bootstrap.bundle.min.js" ba6170d422f7d715ae9ae061f879fb73752f3eeec8108fb75069dcbc0bac30c2
fetch "$CDN/htmx.org@$HTMX_VERSION/dist/htmx.min.js" "$target/htmx.min.js" d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717
fetch "$CDN/alpinejs@$ALPINE_VERSION/dist/cdn.min.js" "$target/alpine.min.js" b4036e9e9ac22fa6f6ae1ba292e015e1f3e9c85d0d79e73e4c8eb9bb139b522e
fetch "$CDN/bootstrap@$BOOTSTRAP_VERSION/LICENSE" "$target/licenses/bootstrap-LICENSE.txt" bc0cd18d94c028f1c0b62ec9fdaddcb9921c1e07c29cf52dc7df8cca9daf2488
fetch "$CDN/htmx.org@$HTMX_VERSION/LICENSE" "$target/licenses/htmx-LICENSE.txt" d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38
fetch "https://raw.githubusercontent.com/alpinejs/alpine/v$ALPINE_VERSION/LICENSE.md" "$target/licenses/alpine-LICENSE.txt" 08b7502da6e7aa1d0bbdc97d220fbf669b9366c61bd0f072238283c89bc4773a
