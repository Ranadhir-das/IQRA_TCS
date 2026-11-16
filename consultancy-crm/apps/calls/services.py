"""Transactional call submission and canonical idempotency handling."""

import hashlib
import hmac
import json
from datetime import timedelta, timezone as datetime_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.leads.models import Lead

from .models import Call, CallOutcome


MAX_CALL_DURATION = timedelta(seconds=86400)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class CallEventConflict(APIException):
    status_code = 409
    default_detail = "This client event ID was already used with a different call payload."
    default_code = "call_event_conflict"


def normalize_employee_notes(value):
    if value is None:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def canonical_timestamp(value):
    return (
        value.astimezone(datetime_timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def canonical_call_fingerprint(
    *, caller_id, client_event_id, lead_id, outcome_code, started_at, ended_at, employee_notes
):
    payload = {
        "schema_version": 1,
        "caller_id": int(caller_id),
        "client_event_id": str(client_event_id).lower(),
        "lead_id": int(lead_id),
        "outcome_code": outcome_code,
        "started_at": canonical_timestamp(started_at),
        "ended_at": canonical_timestamp(ended_at),
        "employee_notes": normalize_employee_notes(employee_notes),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _return_existing_or_conflict(call, fingerprint):
    if hmac.compare_digest(call.request_fingerprint, fingerprint):
        return call, False
    raise CallEventConflict()


def _validate_call_times(*, started_at, ended_at, now):
    if timezone.is_naive(started_at) or timezone.is_naive(ended_at):
        raise ValidationError({"started_at": "Timestamps must include an explicit timezone offset."})
    if ended_at < started_at:
        raise ValidationError({"ended_at": "End time must not be before start time."})

    elapsed = ended_at - started_at
    if elapsed > MAX_CALL_DURATION:
        raise ValidationError({"ended_at": "Call duration cannot exceed 24 hours."})

    latest_allowed = now + MAX_FUTURE_SKEW
    if started_at > latest_allowed or ended_at > latest_allowed:
        raise ValidationError({"ended_at": "Call timestamps cannot be more than five minutes in the future."})

    # The interval was checked before flooring, so a fractional interval above
    # 24 hours cannot pass after conversion to whole seconds.
    return int(elapsed.total_seconds())


def submit_call(
    *,
    caller,
    lead_id,
    client_event_id,
    outcome_code,
    started_at,
    ended_at,
    employee_notes,
):
    """Create one call or return the existing call for an identical retry.

    Lock order for new writes is Lead, caller User, then CallOutcome. The Lead
    lock matches assignment, archive, and bulk-assignment code paths.
    """
    normalized_notes = normalize_employee_notes(employee_notes)
    fingerprint = canonical_call_fingerprint(
        caller_id=caller.pk,
        client_event_id=client_event_id,
        lead_id=lead_id,
        outcome_code=outcome_code,
        started_at=started_at,
        ended_at=ended_at,
        employee_notes=normalized_notes,
    )

    existing = Call.objects.filter(
        caller_id=caller.pk,
        client_event_id=client_event_id,
    ).first()
    if existing is not None:
        return _return_existing_or_conflict(existing, fingerprint)

    with transaction.atomic():
        try:
            lead = Lead.objects.select_for_update().get(pk=lead_id)
        except Lead.DoesNotExist:
            existing = Call.objects.filter(
                caller_id=caller.pk,
                client_event_id=client_event_id,
            ).first()
            if existing is not None:
                return _return_existing_or_conflict(existing, fingerprint)
            raise NotFound("Lead is not available for this call.") from None

        # A concurrent request with this event may have committed while this
        # request waited for the Lead row lock. Resolve it before eligibility
        # checks so retries survive later reassignment or archiving.
        existing = Call.objects.filter(
            caller_id=caller.pk,
            client_event_id=client_event_id,
        ).first()
        if existing is not None:
            return _return_existing_or_conflict(existing, fingerprint)

        if lead.is_archived or lead.assigned_to_id != caller.pk:
            raise NotFound("Lead is not available for this call.")

        try:
            locked_caller = User.objects.select_for_update().get(pk=caller.pk)
        except User.DoesNotExist:
            raise PermissionDenied("An active employee account is required.") from None
        if (
            not locked_caller.is_active
            or locked_caller.role != User.Role.EMPLOYEE
            or locked_caller.is_superuser
        ):
            raise PermissionDenied("An active employee account is required.")

        outcome = (
            CallOutcome.objects.select_for_update()
            .filter(code=outcome_code)
            .first()
        )
        if outcome is None or not outcome.is_active:
            raise ValidationError({"outcome_code": "This call outcome is not available."})

        duration_seconds = _validate_call_times(
            started_at=started_at,
            ended_at=ended_at,
            now=timezone.now(),
        )
        # Recompute after locking the outcome so the inserted fingerprint is
        # bound to the stable code read from the locked database row.
        fingerprint = canonical_call_fingerprint(
            caller_id=locked_caller.pk,
            client_event_id=client_event_id,
            lead_id=lead.pk,
            outcome_code=outcome.code,
            started_at=started_at,
            ended_at=ended_at,
            employee_notes=normalized_notes,
        )

        try:
            # The savepoint keeps the outer transaction usable after a unique
            # constraint violation, allowing the winning event to be queried.
            with transaction.atomic():
                call = Call.objects.create(
                    lead=lead,
                    caller=locked_caller,
                    client_event_id=client_event_id,
                    outcome=outcome,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_seconds=duration_seconds,
                    request_fingerprint=fingerprint,
                    employee_notes=normalized_notes,
                )
        except IntegrityError:
            existing = Call.objects.filter(
                caller_id=locked_caller.pk,
                client_event_id=client_event_id,
            ).first()
            if existing is None:
                raise
            return _return_existing_or_conflict(existing, fingerprint)

        return call, True
