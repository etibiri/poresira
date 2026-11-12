#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Replace the PoreSira database with the content of a backup made by scripts/backup.sh.
# Run from the repository root on the Docker host. DESTROYS the current database content.
set -eu

usage() {
    echo "usage: $0 --yes <backup-file.dump>" >&2
    exit 2
}

[ "${1:-}" = "--yes" ] && [ -n "${2:-}" ] || usage
dump_file="$2"
[ -f "$dump_file" ] || { echo "No such file: $dump_file" >&2; exit 1; }

if [ -f "$dump_file.sha256" ]; then
    (cd "$(dirname "$dump_file")" && sha256sum -c "$(basename "$dump_file").sha256")
fi

compose="docker compose"
$compose stop web worker slurm-agent backup 2>/dev/null || true
$compose up -d db
$compose exec -T db sh -c 'until pg_isready -U postgres -d postgres -q; do sleep 1; done'

$compose exec -T db sh -c '
    psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
        -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_OWNER_USER\""'

$compose exec -T db sh -c \
    'pg_restore --exit-on-error --no-owner -U "$POSTGRES_OWNER_USER" -d "$POSTGRES_DB"' < "$dump_file"

$compose exec -T db /opt/poresira/apply-default-privileges.sh

$compose up -d
$compose exec -T web python manage.py verify_audit_chain
echo "Restore complete."
