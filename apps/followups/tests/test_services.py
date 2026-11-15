import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.leads.models import Lead
from apps.leads.services import set_lead_assignment
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    FollowUpConflict,
    cancel_follow_up,
    complete_follow_up,
    create_follow_up,
    reschedule_follow_up,
)


class FollowUpLifecycleServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            username="followup_service_owner",
            role=User.Role.EMPLOYEE,
            first_name="Service",
            last_name="Owner",
        )
        self.other_employee = User.objects.create(
            username="followup_service_other",
            role=User.Role.EMPLOYEE,
        )
        self.admin = User.objects.create(
            username="followup_service_admin",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.lead = Lead.objects.create(
            name="Service Follow-up Lead",
            phone="9876508661",
            assigned_to=self.owner,
        )
        self.other_lead = Lead.objects.create(
            name="Other Service Lead",
            phone="9876508662",
            assigned_to=self.other_employee,
        )
        self.outcome = CallOutcome.objects.create(code="FU_SERVICE", label="Service outcome")

    def make_call(self, *, lead=None, caller=None):
        ended_at = timezone.now()
        return Call.objects.create(
            lead=lead or self.lead,
            caller=caller or self.owner,
            client_event_id=uuid.uuid4(),
            outcome=self.outcome,
            started_at=ended_at - timedelta(seconds=25),
            ended_at=ended_at,
            duration_seconds=25,
            request_fingerprint="b" * 64,
        )

    def create_follow_up(self, *, call_id=None, actor=None, lead_id=None):
        return create_follow_up(
            actor=actor or self.owner,
            lead_id=lead_id or self.lead.pk,
            scheduled_at=timezone.now() + timedelta(days=1),
            call_id=call_id,
            notes="Initial notes",
        )

    def test_create_for_assigned_active_employee_records_snapshots_and_event(self):
        call = self.make_call()
        call_count = Call.objects.count()
        lead_state = (self.lead.status, self.lead.assigned_to_id)
        follow_up = self.create_follow_up(call_id=call.pk)

        self.assertEqual(follow_up.owner_id, self.owner.pk)
        self.assertEqual(follow_up.lead_id, self.lead.pk)
        self.assertEqual(follow_up.call_id, call.pk)
        self.assertEqual(follow_up.status, FollowUp.Status.PENDING)
        self.assertEqual(follow_up.owner_username_snapshot, self.owner.username)
        self.assertEqual(follow_up.owner_name_snapshot, "Service Owner")
        event = FollowUpEvent.objects.get(follow_up=follow_up)
        self.assertEqual(event.event_type, FollowUpEvent.EventType.CREATED)
        self.assertIsNone(event.previous_status)
        self.assertEqual(event.new_status, FollowUp.Status.PENDING)
        self.assertEqual(event.new_scheduled_at, follow_up.scheduled_at)
        self.assertEqual(event.actor_username_snapshot, self.owner.username)
        self.assertEqual(FollowUpEvent.objects.filter(follow_up=follow_up).count(), 1)
        self.assertEqual(Call.objects.count(), call_count)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status, self.lead.assigned_to_id), lead_state)

    def test_creation_rejects_unassigned_archived_other_employee_inactive_and_superuser(self):
        unassigned = Lead.objects.create(name="Unassigned", phone="9876508663")
        archived = Lead.objects.create(
            name="Archived",
            phone="9876508664",
            assigned_to=self.owner,
            is_archived=True,
        )
        for lead in (unassigned, archived, self.other_lead):
            with self.subTest(lead=lead.pk):
                with self.assertRaises(NotFound):
                    self.create_follow_up(lead_id=lead.pk)

        inactive = User.objects.create(
            username="followup_inactive",
            role=User.Role.EMPLOYEE,
            is_active=False,
        )
        superuser_employee = User.objects.create(
            username="followup_super_employee",
            role=User.Role.EMPLOYEE,
            is_superuser=True,
            is_staff=True,
        )
        for employee in (inactive, superuser_employee):
            with self.subTest(employee=employee.username):
                with self.assertRaises(PermissionDenied):
                    self.create_follow_up(actor=employee)

    def test_creation_requires_explicit_aware_datetime(self):
        with self.assertRaises(ValidationError):
            create_follow_up(
                actor=self.owner,
                lead_id=self.lead.pk,
                scheduled_at=timezone.now().replace(tzinfo=None),
            )

    def test_linked_call_must_match_both_lead_and_employee(self):
        other_lead_call = self.make_call(lead=self.other_lead, caller=self.other_employee)
        other_employee_call = self.make_call(caller=self.other_employee)
        for call in (other_lead_call, other_employee_call):
            with self.subTest(call_id=call.pk):
                with self.assertRaises(ValidationError):
                    self.create_follow_up(call_id=call.pk)

    def test_multiple_followups_for_same_lead_and_call_are_supported(self):
        call = self.make_call()
        first = self.create_follow_up(call_id=call.pk)
        second = self.create_follow_up(call_id=call.pk)
        self.assertEqual(FollowUp.objects.filter(lead=self.lead, call=call).count(), 2)
        self.assertNotEqual(first.pk, second.pk)

    def test_creation_event_failure_rolls_back_followup(self):
        with patch.object(
            FollowUpEvent.objects,
            "_create_from_service",
            side_effect=IntegrityError("failed"),
        ):
            with self.assertRaises(IntegrityError):
                self.create_follow_up()
        self.assertEqual(FollowUp.objects.count(), 0)
        self.assertEqual(FollowUpEvent.objects.count(), 0)

    def test_reschedule_pending_followup_updates_only_permitted_fields_and_records_event(self):
        follow_up = self.create_follow_up()
        old_schedule = follow_up.scheduled_at
        next_schedule = old_schedule + timedelta(days=2)
        updated = reschedule_follow_up(
            follow_up_id=follow_up.pk,
            actor=self.owner,
            scheduled_at=next_schedule,
            notes="Updated notes",
            reason="Employee requested a different time",
        )
        self.assertEqual(updated.owner_id, self.owner.pk)
        self.assertEqual(updated.lead_id, self.lead.pk)
        self.assertEqual(updated.scheduled_at, next_schedule)
        self.assertEqual(updated.notes, "Updated notes")
        event = FollowUpEvent.objects.get(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.RESCHEDULED,
        )
        self.assertEqual(event.previous_scheduled_at, old_schedule)
        self.assertEqual(event.new_scheduled_at, next_schedule)
        self.assertEqual(event.previous_status, FollowUp.Status.PENDING)
        self.assertEqual(event.new_status, FollowUp.Status.PENDING)
        self.assertEqual(event.actor_username_snapshot, self.owner.username)
        self.assertEqual(FollowUpEvent.objects.filter(follow_up=follow_up).count(), 2)

    def test_creation_snapshots_use_locked_database_identity_not_stale_actor(self):
        stale_actor = self.owner
        User.objects.filter(pk=self.owner.pk).update(
            first_name="Current",
            last_name="Database Name",
        )
        follow_up = self.create_follow_up(actor=stale_actor)
        self.assertEqual(follow_up.owner_username_snapshot, "followup_service_owner")
        self.assertEqual(follow_up.owner_name_snapshot, "Current Database Name")
        event = FollowUpEvent.objects.get(follow_up=follow_up)
        self.assertEqual(event.actor_name_snapshot, "Current Database Name")

    def test_other_employee_cannot_reschedule_or_complete_followup(self):
        follow_up = self.create_follow_up()
        original_schedule = follow_up.scheduled_at
        original_notes = follow_up.notes
        with self.assertRaises(NotFound):
            reschedule_follow_up(
                follow_up_id=follow_up.pk,
                actor=self.other_employee,
                scheduled_at=timezone.now() + timedelta(days=2),
            )
        with self.assertRaises(NotFound):
            complete_follow_up(follow_up_id=follow_up.pk, actor=self.other_employee)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.PENDING)
        self.assertEqual(follow_up.scheduled_at, original_schedule)
        self.assertEqual(follow_up.notes, original_notes)
        self.assertEqual(FollowUpEvent.objects.filter(follow_up=follow_up).count(), 1)

    def test_completion_is_terminal_and_has_no_call_or_lead_side_effects(self):
        follow_up = self.create_follow_up()
        before_calls = Call.objects.count()
        before_followups = FollowUp.objects.count()
        lead_state = (self.lead.status, self.lead.assigned_to_id)

        completed = complete_follow_up(follow_up_id=follow_up.pk, actor=self.owner)
        self.assertEqual(completed.status, FollowUp.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)
        self.assertIsNone(completed.cancelled_at)
        event = FollowUpEvent.objects.get(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.COMPLETED,
        )
        self.assertEqual(event.previous_status, FollowUp.Status.PENDING)
        self.assertEqual(event.new_status, FollowUp.Status.COMPLETED)
        self.assertEqual(FollowUpEvent.objects.filter(follow_up=follow_up).count(), 2)
        self.assertEqual(Call.objects.count(), before_calls)
        self.assertEqual(FollowUp.objects.count(), before_followups)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status, self.lead.assigned_to_id), lead_state)

        for action in (
            lambda: complete_follow_up(follow_up_id=follow_up.pk, actor=self.owner),
            lambda: cancel_follow_up(follow_up_id=follow_up.pk, actor=self.owner),
            lambda: reschedule_follow_up(
                follow_up_id=follow_up.pk,
                actor=self.owner,
                scheduled_at=timezone.now() + timedelta(days=3),
            ),
        ):
            with self.assertRaises(FollowUpConflict):
                action()

    def test_employee_cancellation_is_terminal_and_records_optional_reason(self):
        follow_up = self.create_follow_up()
        before_calls = Call.objects.count()
        before_followups = FollowUp.objects.count()
        lead_state = (self.lead.status, self.lead.assigned_to_id)
        cancelled = cancel_follow_up(
            follow_up_id=follow_up.pk,
            actor=self.owner,
            reason="Customer unavailable",
        )
        self.assertEqual(cancelled.status, FollowUp.Status.CANCELLED)
        self.assertIsNotNone(cancelled.cancelled_at)
        self.assertIsNone(cancelled.completed_at)
        event = FollowUpEvent.objects.get(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.CANCELLED,
        )
        self.assertEqual(event.reason, "Customer unavailable")
        self.assertEqual(event.previous_status, FollowUp.Status.PENDING)
        self.assertEqual(event.new_status, FollowUp.Status.CANCELLED)
        self.assertEqual(FollowUpEvent.objects.filter(follow_up=follow_up).count(), 2)
        self.assertEqual(Call.objects.count(), before_calls)
        self.assertEqual(FollowUp.objects.count(), before_followups)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status, self.lead.assigned_to_id), lead_state)
        with self.assertRaises(FollowUpConflict):
            cancel_follow_up(follow_up_id=follow_up.pk, actor=self.owner)

    def test_admin_cancellation_requires_reason_and_supports_deactivated_owner(self):
        follow_up = self.create_follow_up()
        with self.assertRaises(PermissionDenied):
            cancel_follow_up(
                follow_up_id=follow_up.pk,
                actor=self.admin,
                reason="Cannot cancel while owner is active",
            )
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.PENDING)

        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])

        with self.assertRaises(ValidationError):
            cancel_follow_up(follow_up_id=follow_up.pk, actor=self.admin)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.PENDING)

        cancelled = cancel_follow_up(
            follow_up_id=follow_up.pk,
            actor=self.admin,
            reason="Owner deactivated; closing pending task",
        )
        self.assertEqual(cancelled.owner_id, self.owner.pk)
        self.assertEqual(cancelled.status, FollowUp.Status.CANCELLED)
        event = FollowUpEvent.objects.get(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.CANCELLED,
        )
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.actor_username_snapshot, self.admin.username)
        self.assertEqual(event.reason, "Owner deactivated; closing pending task")

    def test_lead_reassignment_does_not_transfer_followup_ownership(self):
        follow_up = self.create_follow_up()
        set_lead_assignment(self.lead, self.other_employee, self.admin)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.owner_id, self.owner.pk)
        self.assertEqual(follow_up.lead_id, self.lead.pk)
        self.assertEqual(complete_follow_up(follow_up_id=follow_up.pk, actor=self.owner).status,
                         FollowUp.Status.COMPLETED)

        with self.assertRaises(NotFound):
            self.create_follow_up(actor=self.owner)

    def test_employee_deactivation_preserves_followup_and_owner_identity_snapshot(self):
        follow_up = self.create_follow_up()
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        follow_up.refresh_from_db()
        self.assertTrue(FollowUp.objects.filter(pk=follow_up.pk).exists())
        self.assertEqual(follow_up.owner_id, self.owner.pk)
        self.assertEqual(follow_up.owner_username_snapshot, "followup_service_owner")

    def test_terminal_transition_event_failure_rolls_back_state_change(self):
        follow_up = self.create_follow_up()
        with patch.object(
            FollowUpEvent.objects,
            "_create_from_service",
            side_effect=IntegrityError("failed"),
        ):
            with self.assertRaises(IntegrityError):
                complete_follow_up(follow_up_id=follow_up.pk, actor=self.owner)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.PENDING)
        self.assertIsNone(follow_up.completed_at)
        self.assertEqual(
            FollowUpEvent.objects.filter(follow_up=follow_up).count(),
            1,
        )
