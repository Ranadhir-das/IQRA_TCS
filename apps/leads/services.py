"""Transactional operations for manual lead assignment.

Application code that changes ``Lead.assigned_to`` must use
``set_lead_assignment`` so the current assignment and history stay together.
Direct ORM or SQL writes can bypass this service and its audit history.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.leads.models import Lead, LeadAssignmentHistory


EMPLOYEE_MUTABLE_LEAD_FIELDS = frozenset(
    {
        "status",
        "course_interest",
        "enquiry_details",
        "alternate_phone",
        "email",
    }
)


def validate_assignment_actor(actor, *, lock=False):
    """Return a current active admin actor, optionally holding a row lock."""
    user_model = get_user_model()
    users = user_model._default_manager
    if lock:
        users = users.select_for_update()
    try:
        current_actor = users.get(pk=getattr(actor, "pk", None))
    except user_model.DoesNotExist:
        raise ValidationError({"changed_by": "An active admin actor is required."})

    if not current_actor.is_active or not current_actor.is_admin:
        raise ValidationError({"changed_by": "An active admin actor is required."})
    return current_actor


def validate_assignment_target(employee, *, lock=False):
    """Return a current eligible employee, or None for explicit unassignment."""
    if employee is None:
        return None

    user_model = get_user_model()
    users = user_model._default_manager.filter(
        role=User.Role.EMPLOYEE,
        is_active=True,
        is_superuser=False,
    )
    if lock:
        users = users.select_for_update()
    try:
        return users.get(pk=getattr(employee, "pk", None))
    except user_model.DoesNotExist:
        raise ValidationError(
            {"assigned_to": "Assignees must be active, non-superuser employees."}
        )


@transaction.atomic
def set_lead_assignment(lead, employee, actor, reason=""):
    """Change a lead's assignee and persist audit history as one operation.

    Returns ``(locked_lead, changed)``. A no-op returns ``False`` and creates
    no history row.
    """
    locked_lead = Lead.objects.select_for_update().get(pk=lead.pk)
    current_actor = validate_assignment_actor(actor, lock=True)
    target_employee = validate_assignment_target(employee, lock=True)

    previous_employee_id = locked_lead.assigned_to_id
    target_employee_id = target_employee.pk if target_employee else None
    if previous_employee_id == target_employee_id:
        return locked_lead, False

    previous_employee = locked_lead.assigned_to
    locked_lead.assigned_to = target_employee
    locked_lead.save(update_fields=["assigned_to", "updated_at"])
    previous_username, previous_name = _identity_snapshot(previous_employee)
    new_username, new_name = _identity_snapshot(target_employee)
    actor_username, actor_name = _identity_snapshot(current_actor)
    LeadAssignmentHistory.objects.create(
        lead=locked_lead,
        previous_employee=previous_employee,
        previous_employee_username=previous_username,
        previous_employee_name=previous_name,
        new_employee=target_employee,
        new_employee_username=new_username,
        new_employee_name=new_name,
        changed_by=current_actor,
        changed_by_username=actor_username,
        changed_by_name=actor_name,
        reason=(reason or "").strip(),
    )
    return locked_lead, True


def _identity_snapshot(user):
    if user is None:
        return None, None
    return user.username, user.get_full_name() or user.username


@transaction.atomic
def update_employee_lead_lifecycle(*, lead_id, actor, **updates):
    """Update permitted qualification fields on an active employee's assigned lead.

    Lock order is Lead then employee User, matching set_lead_assignment,
    submit_call, and create_follow_up.
    """
    try:
        lead = Lead.objects.select_for_update().get(pk=lead_id)
    except Lead.DoesNotExist:
        raise NotFound("Lead not found.") from None

    user_model = get_user_model()
    try:
        employee = user_model._default_manager.select_for_update().get(
            pk=getattr(actor, "pk", None)
        )
    except user_model.DoesNotExist:
        raise PermissionDenied("An active employee account is required.") from None

    if (
        not employee.is_active
        or employee.role != User.Role.EMPLOYEE
        or employee.is_superuser
    ):
        raise PermissionDenied("An active employee account is required.")

    if lead.is_archived or lead.assigned_to_id != employee.pk:
        raise NotFound("Lead not found.")

    if not updates:
        raise ValidationError({"detail": "Provide at least one field to update."})

    update_fields = []
    for field, value in updates.items():
        if field not in EMPLOYEE_MUTABLE_LEAD_FIELDS:
            raise ValidationError({field: "This field is not accepted."})
        setattr(lead, field, value)
        update_fields.append(field)

    lead.full_clean()
    lead.save(update_fields=[*update_fields, "updated_at"])
    return lead
