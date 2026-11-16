"""Minimal lead records for the IQRA TCS calling CRM."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

LEAD_STATUS_VALUES = ("NEW", "CONTACTED", "INTERESTED", "NOT_INTERESTED", "CONVERTED")


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        CONTACTED = "CONTACTED", "Contacted"
        INTERESTED = "INTERESTED", "Interested"
        NOT_INTERESTED = "NOT_INTERESTED", "Not interested"
        CONVERTED = "CONVERTED", "Converted"

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32, db_index=True)
    alternate_phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    course_interest = models.CharField(max_length=200, blank=True)
    enquiry_details = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_iqra_leads",
        limit_choices_to=Q(role="EMPLOYEE", is_active=True, is_superuser=False),
    )
    internal_notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_iqra_leads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="lead_status_created_idx"),
            models.Index(
                fields=["assigned_to", "is_archived", "created_at"],
                name="lead_assignee_archive_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=LEAD_STATUS_VALUES),
                name="lead_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(phone__regex=r"\S"),
                name="lead_phone_non_whitespace",
            ),
        ]

    def clean(self):
        super().clean()
        if self.assigned_to_id is None:
            return

        user_model = self._meta.get_field("assigned_to").remote_field.model
        eligible = user_model._default_manager.filter(
            pk=self.assigned_to_id,
            role="EMPLOYEE",
            is_active=True,
            is_superuser=False,
        ).exists()
        if not eligible:
            raise ValidationError(
                {"assigned_to": "Assignees must be active, non-superuser employees."}
            )

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"


class LeadAssignmentHistory(models.Model):
    lead = models.ForeignKey(
        Lead,
        on_delete=models.PROTECT,
        related_name="assignment_history",
    )
    previous_employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_assignment_history_from",
    )
    previous_employee_username = models.CharField(max_length=150, null=True, blank=True)
    previous_employee_name = models.CharField(max_length=302, null=True, blank=True)
    new_employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_assignment_history_to",
    )
    new_employee_username = models.CharField(max_length=150, null=True, blank=True)
    new_employee_name = models.CharField(max_length=302, null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lead_assignment_changes",
    )
    changed_by_username = models.CharField(max_length=150, null=True, blank=True)
    changed_by_name = models.CharField(max_length=302, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
        indexes = [
            models.Index(
                fields=["lead", "changed_at"],
                name="lead_assign_hist_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Assignment history for lead {self.lead_id} at {self.changed_at}"


class StagedLeadImport(models.Model):
    class DuplicatePolicy(models.TextChoices):
        SKIP = "skip_duplicates", "Skip duplicates"
        ALLOW = "allow_duplicates", "Allow duplicates"

    class State(models.TextChoices):
        STAGED = "STAGED", "Staged"
        PROCESSING = "PROCESSING", "Processing"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staged_lead_imports",
    )
    file_sha256 = models.CharField(max_length=64)
    data_sha256 = models.CharField(max_length=64)
    commit_token_sha256 = models.CharField(max_length=64, unique=True)
    duplicate_policy = models.CharField(
        max_length=32,
        choices=DuplicatePolicy.choices,
        default=DuplicatePolicy.SKIP,
    )
    staged_rows = models.JSONField(default=list)
    preview_summary = models.JSONField(default=dict)
    preview_rows = models.JSONField(default=list)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    processing_started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    commit_result = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["uploaded_by", "state", "expires_at"], name="lead_import_owner_state_idx")]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def __str__(self) -> str:
        return f"Staged lead import {self.pk} ({self.state})"
