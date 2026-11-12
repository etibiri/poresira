# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL building blocks for the audit trail, used from migrations.

Tracking a new model: add ``pghistory.track`` to it, then in a migration call
``attach_audit_chain`` and ``restrict_audit_tables`` for its event table.
"""

from django.conf import settings
from django.db import migrations

CHAIN_TABLE = "core_auditchainlink"
CONTEXT_TABLE = "pghistory_context"
CHAIN_FUNCTION = "poresira_audit_chain_append"

# SECURITY DEFINER: the application role has no write access to the chain table,
# only this trigger (running with the owner's rights) appends to it. The advisory
# lock serialises writers so each link sees the true previous hash.
CREATE_CHAIN_FUNCTIONS = f"""
CREATE FUNCTION {CHAIN_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
SET timezone = 'UTC'
AS $$
DECLARE
    previous text;
    new_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('{CHAIN_FUNCTION}'));
    SELECT row_hash INTO previous FROM {CHAIN_TABLE} ORDER BY seq DESC LIMIT 1;
    previous := COALESCE(previous, '');
    new_hash := encode(sha256(convert_to(
        previous || '|' || TG_TABLE_NAME || '|' || NEW.pgh_id || '|' || to_jsonb(NEW)::text,
        'UTF8')), 'hex');
    INSERT INTO {CHAIN_TABLE} (event_table, event_id, previous_hash, row_hash)
        VALUES (TG_TABLE_NAME, NEW.pgh_id, previous, new_hash);
    RETURN NULL;
END;
$$;

CREATE FUNCTION poresira_audit_chain_verify()
RETURNS TABLE (link_seq bigint, problem text)
LANGUAGE plpgsql
SET search_path = public, pg_temp
SET timezone = 'UTC'
AS $$
DECLARE
    link record;
    chained_table record;
    expected_previous text := '';
    row_text text;
    unlinked_count bigint;
BEGIN
    FOR link IN SELECT * FROM {CHAIN_TABLE} ORDER BY seq LOOP
        IF link.previous_hash <> expected_previous THEN
            link_seq := link.seq;
            problem := 'previous hash does not match the preceding link (link removed or altered)';
            RETURN NEXT;
        END IF;
        EXECUTE format('SELECT to_jsonb(event)::text FROM %I event WHERE pgh_id = $1',
                       link.event_table) INTO row_text USING link.event_id;
        link_seq := link.seq;
        IF row_text IS NULL THEN
            problem := 'audit event row is missing';
            RETURN NEXT;
        ELSIF link.row_hash <> encode(sha256(convert_to(
                link.previous_hash || '|' || link.event_table || '|' || link.event_id
                || '|' || row_text, 'UTF8')), 'hex') THEN
            problem := 'audit event row or its hash was altered';
            RETURN NEXT;
        END IF;
        expected_previous := link.row_hash;
    END LOOP;

    FOR chained_table IN
        SELECT DISTINCT tgrelid::regclass::text AS table_name FROM pg_trigger
        WHERE tgfoid = '{CHAIN_FUNCTION}'::regproc
    LOOP
        EXECUTE format(
            'SELECT count(*) FROM %s event WHERE NOT EXISTS (
                 SELECT 1 FROM {CHAIN_TABLE} link
                 WHERE link.event_table = %L AND link.event_id = event.pgh_id)',
            chained_table.table_name, chained_table.table_name) INTO unlinked_count;
        IF unlinked_count > 0 THEN
            link_seq := NULL;
            problem := unlinked_count || ' event row(s) in ' || chained_table.table_name
                       || ' have no chain link';
            RETURN NEXT;
        END IF;
    END LOOP;
END;
$$;

-- pghistory upserts its context row from inside tracking triggers; run that as owner
-- so the application role needs no UPDATE on the context table.
ALTER FUNCTION _pgh_attach_context() SECURITY DEFINER SET search_path = public, pg_temp;
"""

DROP_CHAIN_FUNCTIONS = f"""
DROP FUNCTION poresira_audit_chain_verify();
DROP FUNCTION {CHAIN_FUNCTION}();
ALTER FUNCTION _pgh_attach_context() SECURITY INVOKER RESET search_path;
"""

# The shared writers (chain table, context table) are written by definer functions only.
FULLY_LOCKED_TABLES = (CHAIN_TABLE, CONTEXT_TABLE)


def create_chain_functions() -> migrations.RunSQL:
    return migrations.RunSQL(CREATE_CHAIN_FUNCTIONS, reverse_sql=DROP_CHAIN_FUNCTIONS)


def attach_audit_chain(event_table: str) -> migrations.RunSQL:
    trigger_name = f"{event_table}_audit_chain"
    return migrations.RunSQL(
        f"CREATE TRIGGER {trigger_name} AFTER INSERT ON {event_table} "
        f"FOR EACH ROW EXECUTE FUNCTION {CHAIN_FUNCTION}();",
        reverse_sql=f"DROP TRIGGER {trigger_name} ON {event_table};",
    )


def restrict_audit_tables(*event_tables: str) -> migrations.RunPython:
    """Revoke write access to audit tables from the application role."""

    def revoke(apps, schema_editor):
        cursor = schema_editor.connection.cursor()
        role = settings.DATABASE_APP_ROLE
        cursor.execute(
            "SELECT current_user = %s, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            [role, role],
        )
        migrating_as_app_role, role_exists = cursor.fetchone()
        if migrating_as_app_role or not role_exists:
            print(
                f"WARNING: audit tables not restricted: role {role!r} "
                "is missing or is the migration user."
            )
            return
        quoted_role = schema_editor.quote_name(role)
        for table in event_tables:
            cursor.execute(
                f"REVOKE UPDATE, DELETE, TRUNCATE ON {schema_editor.quote_name(table)} "
                f"FROM {quoted_role}"
            )
        for table in FULLY_LOCKED_TABLES:
            cursor.execute(
                f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {schema_editor.quote_name(table)} "
                f"FROM {quoted_role}"
            )

    return migrations.RunPython(revoke, migrations.RunPython.noop)
