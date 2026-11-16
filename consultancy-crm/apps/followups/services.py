"""Transactional follow-up lifecycle operations."""

from datetime import datetime, timezone as datetime_timezone
import hashlib
import hmac
import json
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead

from .models import FollowUp, FollowUpEvent


MAX_NOTES_LENGTH = 5000
MAX_REASON_LENGTH = 1000


class FollowUpConflict(APIException):
    status_code = 409
    default_detail = "The follow-up is no longer pending."
    default_code = "follow_up_state_conflict"


class FollowUpIdempotencyConflict(APIException):
    status_code = 409
    default_detail = (
        "This Idempotency-Key was already used with a different follow-up operation or payload."
    )
    default_code = "follow_up_idempotency_conflict"


def canonical_timestamp(value):
    return (
        value.astimezone(datetime_timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _encode_fingerprint(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_create_followup_fingerprint(
    *,
    actor_id,
    idempotency_key,
    lead_id,
    call_id,
    scheduled_at,
    notes,
):
    return _encode_fingerprint(
        {
            "schema_version": 1,
            "operation": FollowUpEvent.EventType.CREATED,
            "actor_id": int(actor_id),
            "idempotency_key": str(idempotency_key).lower(),
            "lead_id": int(lead_id),
            "call_id": int(call_id) if call_id is not None else None,
            "scheduled_at": canonical_timestamp(scheduled_at),
            "notes": notes,
        }
    )


def canonical_reschedule_followup_fingerprint(
    *,
    actor_id,
    idempotency_key,
    follow_up_id,
    scheduled_at,
    notes,
    reason,
):
    return _encode_fingerprint(
        {
            "schema_version": 1,
            "operation": FollowUpEvent.EventType.RESCHEDULED,
            "actor_id": int(actor_id),
            "idempotency_key": str(idempotency_key).lower(),
            "follow_up_id": int(follow_up_id),
            "scheduled_at": canonical_timestamp(scheduled_at),
            "notes": notes,
            "reason": reason,
        }
    )


def _validate_idempotency_key(idempotency_key):
    if idempotency_key is None:
        return None
    if isinstance(idempotency_key, uuid.UUID):
        return idempotency_key
    if isinstance(idempotency_key, str):
        cleaned = idempotency_key.strip()
        try:
            parsed = uuid.UUID(cleaned)
        except ValueError:
            raise ValidationError({"Idempotency-Key": "Enter a valid UUID."}) from None
        if str(parsed) == cleaned.lower():
            return parsed
    raise ValidationError({"Idempotency-Key": "Enter a valid UUID."})


def _find_idempotent_event(*, actor_id, idempotency_key):
    return (
        FollowUpEvent.objects.select_related(
            "follow_up__lead",
            "follow_up__owner",
            "follow_up__call",
        )
        .filter(actor_id=actor_id, idempotency_key=idempotency_key)
        .first()
    )


def _resolve_idempotent_event(
    existing_event,
    *,
    expected_event_type,
    fingerprint,
    expected_follow_up_id=None,
):
    if (
        existing_event.event_type == expected_event_type
        and (
            expected_follow_up_id is None
            or existing_event.follow_up_id == expected_follow_up_id
        )
        and existing_event.request_fingerprint is not None
        and fingerprint is not None
        and hmac.compare_digest(existing_event.request_fingerprint, fingerprint)
    ):
        follow_up = existing_event.follow_up
        follow_up._created = False
        return follow_up
    raise FollowUpIdempotencyConflict()


def _validate_schedule(scheduled_at):
    if not isinstance(scheduled_at, datetime) or timezone.is_naive(scheduled_at):
        raise ValidationError(
            {"scheduled_at": "Provide an ISO-8601 datetime with an explicit timezone offset."}
        )


def _validate_text(value, *, field, max_length, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError({field: "Must be a string."})
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not value:
        raise ValidationError({field: "This field is required."})
    if len(value) > max_length:
        raise ValidationError({field: f"Ensure this field has no more than {max_length} characters."})
    return value


def _lock_active_employee(employee_id):
    try:
        employee = User.objects.select_for_update().get(pk=employee_id)
    except User.DoesNotExist:
        raise PermissionDenied("An active employee account is required.") from None
    if (
        not employee.is_active
        or employee.role != User.Role.EMPLOYEE
        or employee.is_superuser
    ):
        raise PermissionDenied("An active employee account is required.")
    return employee


def _lock_active_actor(actor_id):
    try:
        actor = User.objects.select_for_update().get(pk=actor_id)
    except User.DoesNotExist:
        raise PermissionDenied("An active account is required.") from None
    if not actor.is_active:
        raise PermissionDenied("An active account is required.")
    return actor


def _add_event(
    *,
    follow_up,
    event_type,
    actor,
    previous_scheduled_at=None,
    new_scheduled_at=None,
    previous_status=None,
    new_status=None,
    reason="",
    idempotency_key=None,
    request_fingerprint=None,
):
    return FollowUpEvent.objects._create_from_service(
        follow_up=follow_up,
        event_type=event_type,
        actor=actor,
        previous_scheduled_at=previous_scheduled_at,
        new_scheduled_at=new_scheduled_at,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


@transaction.atomic
def create_follow_up(
    *,
    actor,
    lead_id,
    scheduled_at,
    call_id=None,
    notes="",
    idempotency_key=None,
):
    """Create a pending follow-up and its creation event atomically.

    Lock order is Lead then employee User, matching lead assignment and call
    submission. A linked Call must belong to both this Lead and employee.
    """
    _validate_schedule(scheduled_at)
    notes = _validate_text(notes, field="notes", max_length=MAX_NOTES_LENGTH)
    key_uuid = _validate_idempotency_key(idempotency_key)
    actor_id = getattr(actor, "pk", None)
    fingerprint = (
        canonical_create_followup_fingerprint(
            actor_id=actor_id,
            idempotency_key=key_uuid,
            lead_id=lead_id,
            call_id=call_id,
            scheduled_at=scheduled_at,
            notes=notes,
        )
        if key_uuid is not None and actor_id is not None
        else None
    )

    try:
        lead = Lead.objects.select_for_update().get(pk=lead_id)
    except Lead.DoesNotExist:
        if key_uuid is not None:
            employee = _lock_active_employee(actor_id)
            existing_event = _find_idempotent_event(
                actor_id=employee.pk,
                idempotency_key=key_uuid,
            )
            if existing_event is not None:
                return _resolve_idempotent_event(
                    existing_event,
                    expected_event_type=FollowUpEvent.EventType.CREATED,
                    fingerprint=fingerprint,
                )
        raise NotFound("Lead is not available for a follow-up.") from None

    employee = _lock_active_employee(actor_id)
    if key_uuid is not None:
        existing_event = _find_idempotent_event(
            actor_id=employee.pk,
            idempotency_key=key_uuid,
        )
        if existing_event is not None:
            return _resolve_idempotent_event(
                existing_event,
                expected_event_type=FollowUpEvent.EventType.CREATED,
                fingerprint=fingerprint,
            )

    if lead.is_archived or lead.assigned_to_id != employee.pk:
        raise NotFound("Lead is not available for a follow-up.")

    call = None
    if call_id is not None:
        call = Call.objects.filter(
            pk=call_id,
            lead_id=lead.pk,
            caller_id=employee.pk,
        ).first()
        if call is None:
            raise ValidationError(
                {"call_id": "The call must belong to this lead and employee."}
            )

    try:
        with transaction.atomic():
            follow_up = FollowUp.objects._create_from_service(
                lead=lead,
                owner=employee,
                call=call,
                scheduled_at=scheduled_at,
                status=FollowUp.Status.PENDING,
                notes=notes,
            )
            _add_event(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.CREATED,
                actor=employee,
                new_scheduled_at=follow_up.scheduled_at,
                new_status=FollowUp.Status.PENDING,
                idempotency_key=key_uuid,
                request_fingerprint=fingerprint,
            )
    except IntegrityError:
        if key_uuid is None:
            raise
        existing_event = _find_idempotent_event(
            actor_id=employee.pk,
            idempotency_key=key_uuid,
        )
        if existing_event is None:
            raise
        return _resolve_idempotent_event(
            existing_event,
            expected_event_type=FollowUpEvent.EventType.CREATED,
            fingerprint=fingerprint,
        )

    follow_up._created = True
    return follow_up


def _lock_owned_pending_follow_up(*, follow_up_id, actor_id):
    try:
        follow_up = FollowUp.objects.select_for_update().get(
            pk=follow_up_id,
            owner_id=actor_id,
        )
    except FollowUp.DoesNotExist:
        raise NotFound("Follow-up not found.") from None
    if follow_up.status != FollowUp.Status.PENDING:
        raise FollowUpConflict()
    return follow_up


@transaction.atomic
def reschedule_follow_up(
    *,
    follow_up_id,
    actor,
    scheduled_at,
    notes=None,
    reason="",
    idempotency_key=None,
):
    """Change a pending follow-up schedule and append its audit event."""
    _validate_schedule(scheduled_at)
    if notes is not None:
        notes = _validate_text(notes, field="notes", max_length=MAX_NOTES_LENGTH)
    reason = _validate_text(reason, field="reason", max_length=MAX_REASON_LENGTH)
    key_uuid = _validate_idempotency_key(idempotency_key)
    actor_id = getattr(actor, "pk", None)
    fingerprint = (
        canonical_reschedule_followup_fingerprint(
            actor_id=actor_id,
            idempotency_key=key_uuid,
            follow_up_id=follow_up_id,
            scheduled_at=scheduled_at,
            notes=notes,
            reason=reason,
        )
        if key_uuid is not None and actor_id is not None
        else None
    )

    try:
        follow_up = FollowUp.objects.select_for_update().get(
            pk=follow_up_id,
            owner_id=actor_id,
        )
    except FollowUp.DoesNotExist:
        if key_uuid is not None:
            employee = _lock_active_employee(actor_id)
            existing_event = _find_idempotent_event(
                actor_id=employee.pk,
                idempotency_key=key_uuid,
            )
            if existing_event is not None:
                return _resolve_idempotent_event(
                    existing_event,
                    expected_event_type=FollowUpEvent.EventType.RESCHEDULED,
                    fingerprint=fingerprint,
                    expected_follow_up_id=follow_up_id,
                )
        raise NotFound("Follow-up not found.") from None

    employee = _lock_active_employee(actor_id)
    if key_uuid is not None:
        existing_event = _find_idempotent_event(
            actor_id=employee.pk,
            idempotency_key=key_uuid,
        )
        if existing_event is not None:
            return _resolve_idempotent_event(
                existing_event,
                expected_event_type=FollowUpEvent.EventType.RESCHEDULED,
                fingerprint=fingerprint,
                expected_follow_up_id=follow_up.pk,
            )

    if follow_up.status != FollowUp.Status.PENDING:
        raise FollowUpConflict()

    previous_scheduled_at = follow_up.scheduled_at
    follow_up.scheduled_at = scheduled_at
    update_fields = ["scheduled_at", "updated_at"]
    if notes is not None:
        follow_up.notes = notes
        update_fields.append("notes")

    try:
        with transaction.atomic():
            FollowUp.objects._save_from_service(follow_up, update_fields=update_fields)
            _add_event(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.RESCHEDULED,
                actor=employee,
                previous_scheduled_at=previous_scheduled_at,
                new_scheduled_at=follow_up.scheduled_at,
                previous_status=FollowUp.Status.PENDING,
                new_status=FollowUp.Status.PENDING,
                reason=reason,
                idempotency_key=key_uuid,
                request_fingerprint=fingerprint,
            )
    except IntegrityError:
        if key_uuid is None:
            raise
        existing_event = _find_idempotent_event(
            actor_id=employee.pk,
            idempotency_key=key_uuid,
        )
        if existing_event is None:
            raise
        return _resolve_idempotent_event(
            existing_event,
            expected_event_type=FollowUpEvent.EventType.RESCHEDULED,
            fingerprint=fingerprint,
            expected_follow_up_id=follow_up.pk,
        )

    follow_up._created = False
    return follow_up


@transaction.atomic
def complete_follow_up(*, follow_up_id, actor):
    """Complete an owned pending follow-up without creating other records."""
    follow_up = _lock_owned_pending_follow_up(
        follow_up_id=follow_up_id,
        actor_id=getattr(actor, "pk", None),
    )
    employee = _lock_active_employee(getattr(actor, "pk", None))
    previous_status = follow_up.status
    follow_up.status = FollowUp.Status.COMPLETED
    follow_up.completed_at = timezone.now()
    FollowUp.objects._save_from_service(
        follow_up,
        update_fields=["status", "completed_at", "updated_at"],
    )
    _add_event(
        follow_up=follow_up,
        event_type=FollowUpEvent.EventType.COMPLETED,
        actor=employee,
        previous_status=previous_status,
        new_status=FollowUp.Status.COMPLETED,
    )
    return follow_up


@transaction.atomic
def cancel_follow_up(*, follow_up_id, actor, reason=""):
    """Cancel an owned follow-up, or let an active admin cancel one for an inactive owner.

    Admin cancellation is restricted to pending follow-ups whose owner is
    inactive and requires a nonblank reason. Ownership is never changed.
    """
    actor_id = getattr(actor, "pk", None)
    # Scope ordinary employee lookups in SQL before retrieving a row. For a
    # claimed admin, recheck the actor and owner under lock before permitting
    # cross-owner cancellation.
    query = FollowUp.objects.select_for_update()
    if not getattr(actor, "is_admin", False):
        query = query.filter(owner_id=actor_id)
    try:
        follow_up = query.get(pk=follow_up_id)
    except FollowUp.DoesNotExist:
        raise NotFound("Follow-up not found.") from None

    if follow_up.owner_id == actor_id:
        current_actor = _lock_active_actor(actor_id)
        owner = current_actor
    else:
        locked_users = {
            user.pk: user
            for user in User.objects.select_for_update()
            .filter(pk__in={actor_id, follow_up.owner_id})
            .order_by("pk")
        }
        current_actor = locked_users.get(actor_id)
        if current_actor is None or not current_actor.is_active:
            raise PermissionDenied("An active account is required.")
        owner = locked_users.get(follow_up.owner_id)

    is_admin = current_actor.is_admin
    is_owner_employee = (
        current_actor.role == User.Role.EMPLOYEE
        and not current_actor.is_superuser
        and follow_up.owner_id == current_actor.pk
    )
    if not is_admin and not is_owner_employee:
        raise NotFound("Follow-up not found.")

    if is_admin and (owner is None or owner.is_active):
        raise PermissionDenied(
            "Administrators can only cancel follow-ups for inactive employees."
        )

    if follow_up.status != FollowUp.Status.PENDING:
        raise FollowUpConflict()

    reason = _validate_text(
        reason,
        field="reason",
        max_length=MAX_REASON_LENGTH,
        required=is_admin,
    )
    previous_status = follow_up.status
    follow_up.status = FollowUp.Status.CANCELLED
    follow_up.cancelled_at = timezone.now()
    FollowUp.objects._save_from_service(
        follow_up,
        update_fields=["status", "cancelled_at", "updated_at"],
    )
    _add_event(
        follow_up=follow_up,
        event_type=FollowUpEvent.EventType.CANCELLED,
        actor=current_actor,
        previous_status=previous_status,
        new_status=FollowUp.Status.CANCELLED,
        reason=reason,
    )
    return follow_up
