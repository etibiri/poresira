#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Default privileges are stored per database and are not part of a dump, so this runs both at
# first initialisation and after a restore. Migrations then narrow them on audit tables.
set -eu

psql -v ON_ERROR_STOP=1 --username postgres --dbname "$POSTGRES_DB" <<SQL
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_OWNER_USER} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${POSTGRES_APP_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_OWNER_USER} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ${POSTGRES_APP_USER};
SQL
