from django.db import migrations


DEFAULT_OUTCOMES = (
    ("NO_ANSWER", "No answer"),
    ("CALLBACK_REQUESTED", "Callback requested"),
    ("INTERESTED", "Interested"),
    ("NOT_INTERESTED", "Not interested"),
    ("INVALID_NUMBER", "Invalid number"),
)


def seed_default_outcomes(apps, schema_editor):
    CallOutcome = apps.get_model("calls", "CallOutcome")
    manager = CallOutcome.objects.using(schema_editor.connection.alias)
    for code, label in DEFAULT_OUTCOMES:
        manager.get_or_create(code=code, defaults={"label": label, "is_active": True})


class Migration(migrations.Migration):
    dependencies = [("calls", "0001_initial")]

    operations = [
        # Keep the reverse operation non-destructive: seeded outcomes may have
        # acquired call references since this migration was applied.
        migrations.RunPython(seed_default_outcomes, migrations.RunPython.noop),
    ]
