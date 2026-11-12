#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Dump the PoreSira database to BACKUP_DIR as a timestamped custom-format file with a checksum.
# Optional BACKUP_OWNER (uid:gid) hands the files to a host user when run in a container.
# Reads POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD.
set -eu

backup_dir="${BACKUP_DIR:-/backups}"
keep_days="${BACKUP_KEEP_DAYS:-30}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

mkdir -p "$backup_dir"
backup_name="poresira-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial_file="$backup_dir/$backup_name.partial"

# --no-owner keeps the file restorable by any role; ACLs are kept on purpose, they carry
# the read-only protection of the audit tables.
pg_dump --host "${POSTGRES_HOST:-localhost}" --port "${POSTGRES_PORT:-5432}" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --format custom --no-owner --file "$partial_file"

pg_restore --list "$partial_file" > /dev/null
mv "$partial_file" "$backup_dir/$backup_name"
(cd "$backup_dir" && sha256sum "$backup_name" > "$backup_name.sha256")
if [ -n "${BACKUP_OWNER:-}" ]; then
    chown "$BACKUP_OWNER" "$backup_dir/$backup_name" "$backup_dir/$backup_name.sha256"
fi

find "$backup_dir" -name 'poresira-*.dump*' -mtime +"$keep_days" -delete
echo "Backup written: $backup_dir/$backup_name"
