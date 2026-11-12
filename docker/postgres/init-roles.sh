#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs once on first database initialisation. The owner role runs migrations;
# the application role only gets data privileges, narrowed further by migrations.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE ${POSTGRES_APP_USER} LOGIN PASSWORD '${POSTGRES_APP_PASSWORD}';
CREATE ROLE ${POSTGRES_OWNER_USER} LOGIN CREATEDB PASSWORD '${POSTGRES_OWNER_PASSWORD}';
-- Lets the owner (migrations, tests) act as the application role with SET ROLE.
GRANT ${POSTGRES_APP_USER} TO ${POSTGRES_OWNER_USER};
ALTER DATABASE ${POSTGRES_DB} OWNER TO ${POSTGRES_OWNER_USER};
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_APP_USER};
GRANT USAGE ON SCHEMA public TO ${POSTGRES_APP_USER};
SQL

/opt/poresira/apply-default-privileges.sh
