"""Follow-up records and immutable lifecycle events."""

from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.deletion import ProtectedError


_FOLLOWUP_WRITE_AUTHORIZATION = ContextVar("followup_write_authorization", default=None)
_EVENT_WRITE_AUTHORIZATION = ContextVar("followup_event_write_authorization", default=None)


def _save_with_service_authorization(context_var, instance, operation, *, save_kwargs):
    """Authorize one model instance operation, then always clear the token.

    This is an application-level guard against ordinary ORM writes, not a
    security boundary against arbitrary Python code or privileged SQL.
    """
    token = context_var.set((id(instance), operation))
    try:
        instance.save(**save_kwargs)
    finally:
        context_var.reset(token)


class ImmutableEventQuerySet(models.QuerySet):
    def create(self, **kwargs):
        raise ValidationError("Follow-up events can only be created by lifecycle services.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Follow-up events can only be created by lifecycle services.")

    def update(self, **kwargs):
        raise ValidationError("Follow-up events are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Follow-up events are append-only.")

    def delete(self):
        raise ProtectedError("Follow-up events cannot be deleted.", self.model.objects.none())


class ImmutableEventManager(models.Manager.from_queryset(ImmutableEventQuerySet)):
    def _create_from_service(self, **kwargs):
        event = self.model(**kwargs)
        _save_with_service_authorization(
            _EVENT_WRITE_AUTHORIZATION,
            event,
            "create",
            save_kwargs={"using": self.db, "force_insert": True},
        )
        return event


class FollowUpQuerySet(models.QuerySet):
    def create(self, **kwargs):
        raise ValidationError("Follow-ups must be created through lifecycle services.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Follow-ups must be created through lifecycle services.")

    def update(self, **kwargs):
        raise ValidationError("Follow-ups must be changed through lifecycle services.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Follow-ups must be changed through lifecycle services.")

    def delete(self):
        raise ProtectedError("Follow-ups are historical records and cannot be deleted.", self)


class FollowUpManager(models.Manager.from_queryset(FollowUpQuerySet)):
    def _create_from_service(self, **kwargs):
        follow_up = self.model(**kwargs)
        _save_with_service_authorization(
            _FOLLOWUP_WRITE_AUTHORIZATION,
            follow_up,
            "create",
            save_kwargs={"using": self.db, "force_insert": True},
        )
        return follow_up

    def _save_from_service(self, follow_up, *, update_fields):
        _save_with_service_authorization(
            _FOLLOWUP_WRITE_AUTHORIZATION,
            follow_up,
            "update",
            save_kwargs={"using": self.db, "update_fields": update_fields},
        )
        return follow_up


class FollowUp(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.PROTECT,
        related_name="follow_ups",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_follow_ups",
        db_index=False,
    )
    call = models.ForeignKey(
        "calls.Call",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="follow_ups",
    )
    owner_username_snapshot = models.CharField(max_length=150, editable=False)
    owner_name_snapshot = models.CharField(max_length=302, editable=False)
    scheduled_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = FollowUpManager()

    class Meta:
        ordering = ["scheduled_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=("PENDING", "COMPLETED", "CANCELLED")),
                name="followup_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="PENDING",
                        completed_at__isnull=True,
                        cancelled_at__isnull=True,
                    )
                    | Q(
                        status="COMPLETED",
                        completed_at__isnull=False,
                        cancelled_at__isnull=True,
                    )
                    | Q(
                        status="CANCELLED",
                        completed_at__isnull=True,
                        cancelled_at__isnull=False,
                    )
                ),
                name="followup_status_timestamps_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["owner", "status", "scheduled_at", "id"],
                name="followup_owner_status_due_idx",
            ),
            models.Index(
                fields=["status", "scheduled_at", "id"],
                name="followup_status_due_idx",
            ),
        ]

    _IMMUTABLE_FIELDS = (
        "lead_id",
        "owner_id",
        "call_id",
        "created_at",
        "owner_username_snapshot",
        "owner_name_snapshot",
    )
    _TERMINAL_MUTABLE_FIELDS = (
        "scheduled_at",
        "notes",
        "status",
        "completed_at",
        "cancelled_at",
    )

    def save(self, *args, **kwargs):
        operation = "create" if self._state.adding else "update"
        if _FOLLOWUP_WRITE_AUTHORIZATION.get() != (id(self), operation):
            raise ValidationError("Follow-ups can only be written through lifecycle services.")
        if self._state.adding:
            owner = self.owner
            self.owner_username_snapshot = owner.username
            self.owner_name_snapshot = owner.get_full_name() or owner.username
        elif self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "lead_id",
                "owner_id",
                "call_id",
                "created_at",
                "owner_username_snapshot",
                "owner_name_snapshot",
                "status",
                "scheduled_at",
                "notes",
                "completed_at",
                "cancelled_at",
            ).first()
            if original:
                changed_immutable = {
                    field: "Follow-up lead, owner, call and owner snapshots are immutable."
                    for field in self._IMMUTABLE_FIELDS
                    if original[field] != getattr(self, field)
                }
                if changed_immutable:
                    raise ValidationError(changed_immutable)
                if original["status"] in {self.Status.COMPLETED, self.Status.CANCELLED}:
                    changed_terminal = {
                        field: "Terminal follow-ups cannot be changed."
                        for field in self._TERMINAL_MUTABLE_FIELDS
                        if original[field] != getattr(self, field)
                    }
                    if changed_terminal:
                        raise ValidationError(changed_terminal)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Follow-ups are historical records and cannot be deleted.", [self])

    def __str__(self):
        return f"Follow-up {self.pk} for lead {self.lead_id} ({self.status})"


class FollowUpEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "CREATED", "Created"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    follow_up = models.ForeignKey(
        FollowUp,
        on_delete=models.PROTECT,
        related_name="events",
        db_index=False,
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="follow_up_events",
    )
    actor_username_snapshot = models.CharField(max_length=150, editable=False)
    actor_name_snapshot = models.CharField(max_length=302, editable=False)
    previous_scheduled_at = models.DateTimeField(null=True, blank=True)
    new_scheduled_at = models.DateTimeField(null=True, blank=True)
    previous_status = models.CharField(
        max_length=16,
        choices=FollowUp.Status.choices,
        null=True,
        blank=True,
    )
    new_status = models.CharField(
        max_length=16,
        choices=FollowUp.Status.choices,
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.UUIDField(null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    objects = ImmutableEventManager()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(event_type__in=("CREATED", "RESCHEDULED", "COMPLETED", "CANCELLED")),
                name="followup_event_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        event_type="CREATED",
                        previous_status__isnull=True,
                        new_status="PENDING",
                        previous_scheduled_at__isnull=True,
                        new_scheduled_at__isnull=False,
                    )
                    | Q(
                        event_type="RESCHEDULED",
                        previous_status="PENDING",
                        new_status="PENDING",
                        previous_scheduled_at__isnull=False,
                        new_scheduled_at__isnull=False,
                    )
                    | Q(
                        event_type="COMPLETED",
                        previous_status="PENDING",
                        new_status="COMPLETED",
                        previous_scheduled_at__isnull=True,
                        new_scheduled_at__isnull=True,
                    )
                    | Q(
                        event_type="CANCELLED",
                        previous_status="PENDING",
                        new_status="CANCELLED",
                        previous_scheduled_at__isnull=True,
                        new_scheduled_at__isnull=True,
                    )
                ),
                name="followup_event_transition_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(idempotency_key__isnull=True, request_fingerprint__isnull=True)
                    | Q(idempotency_key__isnull=False, request_fingerprint__isnull=False)
                ),
                name="followup_event_idempotency_pair",
            ),
            models.UniqueConstraint(
                fields=["actor", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="followup_event_actor_key_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["follow_up", "created_at", "id"],
                name="followup_event_time_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if (
            self._state.adding
            and _EVENT_WRITE_AUTHORIZATION.get() != (id(self), "create")
        ):
            raise ValidationError("Follow-up events can only be created by lifecycle services.")
        if not self._state.adding:
            raise ValidationError("Follow-up events are append-only.")
        actor = self.actor
        self.actor_username_snapshot = actor.username
        self.actor_name_snapshot = actor.get_full_name() or actor.username
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Follow-up events are append-only.", [self])

    def __str__(self):
        return f"{self.event_type} for follow-up {self.follow_up_id}"
