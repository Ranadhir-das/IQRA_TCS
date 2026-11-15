from django.db import migrations


CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION calls_prevent_outcome_code_change()
RETURNS trigger AS $$
BEGIN
    IF NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION 'CallOutcome.code is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CREATE_TRIGGER = """
CREATE TRIGGER calls_outcome_code_immutable
BEFORE UPDATE OF code ON calls_calloutcome
FOR EACH ROW EXECUTE FUNCTION calls_prevent_outcome_code_change();
"""


def install_outcome_code_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_FUNCTION)
    schema_editor.execute(CREATE_TRIGGER)


def remove_outcome_code_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP TRIGGER IF EXISTS calls_outcome_code_immutable ON calls_calloutcome;")
    schema_editor.execute("DROP FUNCTION IF EXISTS calls_prevent_outcome_code_change();")


class Migration(migrations.Migration):
    dependencies = [("calls", "0002_seed_default_outcomes")]

    operations = [
        migrations.RunPython(install_outcome_code_trigger, remove_outcome_code_trigger),
    ]
