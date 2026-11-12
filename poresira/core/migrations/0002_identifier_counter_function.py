# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

# SECURITY DEFINER lets the application role draw numbers without CREATE rights on
# the schema. A concurrent first call loses the CREATE race and falls through to nextval.
CREATE_FUNCTION = """
CREATE FUNCTION poresira_next_identifier_counter(type_code text, year_code text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    sequence_name text;
BEGIN
    IF type_code !~ '^[A-Z]{1,3}$' OR year_code !~ '^[0-9]{2}$' THEN
        RAISE EXCEPTION 'Invalid identifier sequence key: %, %', type_code, year_code;
    END IF;
    sequence_name := format('poresira_id_%s_%s', lower(type_code), year_code);
    IF to_regclass(sequence_name) IS NULL THEN
        BEGIN
            EXECUTE format('CREATE SEQUENCE %I', sequence_name);
        EXCEPTION WHEN duplicate_table OR unique_violation THEN
            NULL;
        END;
    END IF;
    RETURN nextval(sequence_name::regclass);
END;
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.RunSQL(
            CREATE_FUNCTION,
            reverse_sql="DROP FUNCTION poresira_next_identifier_counter(text, text);",
        ),
    ]
