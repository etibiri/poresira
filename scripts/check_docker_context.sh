#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# What a `docker build` would send: build a throwaway image that only copies the build context,
# then list it. Fails if a private or oversized file would enter the image. Run from the project
# folder. Needs Docker.
set -eu

image="poresira-context-check:$$"
trap 'docker rmi -f "$image" >/dev/null 2>&1 || true' EXIT

printf 'FROM alpine:3\nCOPY . /context\n' | docker build -q -t "$image" -f - . >/dev/null
listing=$(docker run --rm "$image" find /context -type f)

status=0
for forbidden in \
    /context/AMF735 /context/NAME_Thesis_DATA /context/demo-data /context/backups \
    /context/private-data /context/.env /context/.git /context/site /context/docs \
    /context/Samples_Barcode_correspondance.xlsx /context/PoreSira_logo \
    /context/Manuscrit /context/Illustrations; do
    if printf '%s\n' "$listing" | grep -q "^$forbidden"; then
        echo "FORBIDDEN in the build context: $forbidden" >&2
        status=1
    fi
done
for needed in /context/manage.py /context/pyproject.toml /context/uv.lock \
    /context/config/settings/base.py /context/poresira/core/models.py \
    /context/static/vendor/bootstrap.min.css /context/templates; do
    if ! printf '%s\n' "$listing" | grep -q "^$needed"; then
        echo "MISSING from the build context: $needed" >&2
        status=1
    fi
done
echo "$(printf '%s\n' "$listing" | wc -l) files in the build context."
exit "$status"
