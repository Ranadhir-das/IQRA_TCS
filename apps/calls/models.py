"""Call history and administrator-configured call outcomes."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q


OUTCOME_CODE_VALIDATOR = RegexValidator(
    regex=r"\A[A-Z][A-Z0-9_]{0,49}\Z",
    message="Use 1–50 uppercase letters, digits, or underscores; start with a letter.",
)
SHA256_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Fingerprint must be a lowercase SHA-256 hex digest.",
)


class CallOutcome(models.Model):
    code = models.CharField(
        max_length=50,
        unique=True,
        validators=[OUTCOME_CODE_VALIDATOR],
    )
    label = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "label"]
        constraints = [
            models.CheckConstraint(
                condition=Q(code__regex=r"\A[A-Z][A-Z0-9_]{0,49}\Z"),
                name="call_outcome_code_format",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original_code = (
                type(self).objects.filter(pk=self.pk)
                .values_list("code", flat=True)
                .first()
            )
            if original_code is not None and original_code != self.code:
                raise ValidationError({"code": "Outcome codes cannot be changed after creation."})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.label}"


class Call(models.Model):
    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.PROTECT,
        related_name="calls",
        db_index=False,
    )
    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_calls",
        db_index=False,
    )
    client_event_id = models.UUIDField()
    outcome = models.ForeignKey(
        CallOutcome,
        on_delete=models.PROTECT,
        related_name="calls",
    )
    outcome_code_snapshot = models.CharField(max_length=50, editable=False)
    outcome_label_snapshot = models.CharField(max_length=200, editable=False)
    caller_username_snapshot = models.CharField(max_length=150, editable=False)
    caller_name_snapshot = models.CharField(max_length=302, editable=False)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    duration_seconds = models.PositiveIntegerField()
    request_fingerprint = models.CharField(
        max_length=64,
        validators=[SHA256_VALIDATOR],
    )
    employee_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["caller", "client_event_id"],
                name="call_caller_event_uniq",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__gte=F("started_at")),
                name="call_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(duration_seconds__lte=86400),
                name="call_duration_at_most_24h",
            ),
            models.CheckConstraint(
                condition=Q(request_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="call_req_fingerprint_sha256",
            ),
        ]
        indexes = [
            models.Index(fields=["caller", "started_at"], name="call_caller_started_idx"),
            models.Index(fields=["lead", "started_at"], name="call_lead_started_idx"),
            models.Index(fields=["started_at"], name="call_started_idx"),
        ]

    _SNAPSHOT_FIELDS = (
        "outcome_code_snapshot",
        "outcome_label_snapshot",
        "caller_username_snapshot",
        "caller_name_snapshot",
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            outcome = self.outcome
            caller = self.caller
            self.outcome_code_snapshot = outcome.code
            self.outcome_label_snapshot = outcome.label
            self.caller_username_snapshot = caller.username
            self.caller_name_snapshot = caller.get_full_name() or caller.username
        elif self.pk:
            original = type(self).objects.filter(pk=self.pk).values(*self._SNAPSHOT_FIELDS).first()
            if original:
                changed = {
                    field: "Call history snapshots cannot be changed after creation."
                    for field in self._SNAPSHOT_FIELDS
                    if original[field] != getattr(self, field)
                }
                if changed:
                    raise ValidationError(changed)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Call {self.pk} for lead {self.lead_id}"
