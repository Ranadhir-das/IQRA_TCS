import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("leads", "0008_stagedleadimport_processing_started_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="CallOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50, unique=True, validators=[django.core.validators.RegexValidator(message="Use 1–50 uppercase letters, digits, or underscores; start with a letter.", regex="^[A-Z][A-Z0-9_]{0,49}$")])),
                ("label", models.CharField(max_length=200)),
                ("is_active", models.BooleanField(default=True)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["display_order", "label"],
            },
        ),
        migrations.CreateModel(
            name="Call",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client_event_id", models.UUIDField()),
                ("outcome_code_snapshot", models.CharField(editable=False, max_length=50)),
                ("outcome_label_snapshot", models.CharField(editable=False, max_length=200)),
                ("caller_username_snapshot", models.CharField(editable=False, max_length=150)),
                ("caller_name_snapshot", models.CharField(editable=False, max_length=302)),
                ("started_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField()),
                ("duration_seconds", models.PositiveIntegerField()),
                ("request_fingerprint", models.CharField(max_length=64, validators=[django.core.validators.RegexValidator(message="Fingerprint must be a lowercase SHA-256 hex digest.", regex="^[0-9a-f]{64}$")])),
                ("employee_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("caller", models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name="submitted_calls", to=settings.AUTH_USER_MODEL)),
                ("lead", models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name="calls", to="leads.lead")),
                ("outcome", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="calls", to="calls.calloutcome")),
            ],
            options={
                "ordering": ["-started_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="calloutcome",
            constraint=models.CheckConstraint(condition=models.Q(("code__regex", "^[A-Z][A-Z0-9_]{0,49}$")), name="call_outcome_code_format"),
        ),
        migrations.AddConstraint(
            model_name="call",
            constraint=models.UniqueConstraint(fields=("caller", "client_event_id"), name="call_caller_event_uniq"),
        ),
        migrations.AddConstraint(
            model_name="call",
            constraint=models.CheckConstraint(condition=models.Q(("ended_at__gte", models.F("started_at"))), name="call_end_not_before_start"),
        ),
        migrations.AddConstraint(
            model_name="call",
            constraint=models.CheckConstraint(condition=models.Q(("duration_seconds__lte", 86400)), name="call_duration_at_most_24h"),
        ),
        migrations.AddConstraint(
            model_name="call",
            constraint=models.CheckConstraint(condition=models.Q(("request_fingerprint__regex", "^[0-9a-f]{64}$")), name="call_req_fingerprint_sha256"),
        ),
        migrations.AddIndex(
            model_name="call",
            index=models.Index(fields=["caller", "started_at"], name="call_caller_started_idx"),
        ),
        migrations.AddIndex(
            model_name="call",
            index=models.Index(fields=["lead", "started_at"], name="call_lead_started_idx"),
        ),
        migrations.AddIndex(
            model_name="call",
            index=models.Index(fields=["started_at"], name="call_started_idx"),
        ),
    ]
