import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone
from unittest import skipUnless

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.leads.models import Lead
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    cancel_follow_up,
    complete_follow_up,
    create_follow_up as create_follow_up_service,
)


class FollowUpModelTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            username="followup_model_owner",
            role=User.Role.EMPLOYEE,
            first_name="Model",
            last_name="Owner",
        )
        self.other_owner = User.objects.create(
            username="followup_model_other",
            role=User.Role.EMPLOYEE,
        )
        self.admin = User.objects.create(
            username="followup_model_admin",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.lead = Lead.objects.create(
            name="Follow-up Student",
            phone="9876508551",
            assigned_to=self.owner,
        )
        self.outcome = CallOutcome.objects.create(code="FU_MODEL", label="Model test")

    def make_call(self):
        ended_at = timezone.now()
        return Call.objects.create(
            lead=self.lead,
            caller=self.owner,
            client_event_id=uuid.uuid4(),
            outcome=self.outcome,
            started_at=ended_at - timedelta(seconds=10),
            ended_at=ended_at,
            duration_seconds=10,
            request_fingerprint="a" * 64,
        )

    def make_follow_up(self, *, call=None, status=FollowUp.Status.PENDING):
        follow_up = create_follow_up_service(
            actor=self.owner,
            lead_id=self.lead.pk,
            call_id=call.pk if call else None,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        if status == FollowUp.Status.COMPLETED:
            return complete_follow_up(follow_up_id=follow_up.pk, actor=self.owner)
        if status == FollowUp.Status.CANCELLED:
            return cancel_follow_up(follow_up_id=follow_up.pk, actor=self.owner)
        return follow_up

    def test_multiple_followups_may_share_the_same_lead_and_call(self):
        call = self.make_call()
        first = self.make_follow_up(call=call)
        second = self.make_follow_up(call=call)
        self.assertEqual(FollowUp.objects.filter(lead=self.lead, call=call).count(), 2)
        self.assertNotEqual(first.pk, second.pk)

    def test_snapshots_are_generated_and_immutable(self):
        follow_up = self.make_follow_up()
        self.assertEqual(follow_up.owner_username_snapshot, "followup_model_owner")
        self.assertEqual(follow_up.owner_name_snapshot, "Model Owner")

        follow_up.owner_username_snapshot = "forged"
        with self.assertRaises(ValidationError):
            follow_up.save()
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.owner_username_snapshot, "followup_model_owner")

    def test_owner_lead_and_call_are_immutable_after_creation(self):
        call = self.make_call()
        follow_up = self.make_follow_up(call=call)
        new_lead = Lead.objects.create(name="New lead", phone="9876508552")

        for field, value in (
            ("owner", self.other_owner),
            ("lead", new_lead),
            ("call", None),
        ):
            with self.subTest(field=field):
                setattr(follow_up, field, value)
                with self.assertRaises(ValidationError):
                    follow_up.save()
                follow_up.refresh_from_db()

        with self.assertRaises(ValidationError):
            FollowUp.objects.filter(pk=follow_up.pk).update(owner=self.other_owner)

    def test_status_and_terminal_timestamp_database_constraints(self):
        invalid_values = (
            {"status": "UNKNOWN"},
            {"status": FollowUp.Status.COMPLETED},
            {"status": FollowUp.Status.CANCELLED},
            {
                "status": FollowUp.Status.COMPLETED,
                "completed_at": timezone.now(),
                "cancelled_at": timezone.now(),
            },
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        FollowUp.objects._create_from_service(
                            lead=self.lead,
                            owner=self.owner,
                            scheduled_at=timezone.now(),
                            **overrides,
                        )

    def test_required_relations_and_deletion_are_protected(self):
        call = self.make_call()
        follow_up = create_follow_up_service(
            actor=self.owner,
            lead_id=self.lead.pk,
            call_id=call.pk,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        event = FollowUpEvent.objects.get(follow_up=follow_up)
        self.assertEqual(event.actor_username_snapshot, self.owner.username)

        for record in (follow_up, event, self.lead, self.owner):
            with self.subTest(record=type(record).__name__):
                with self.assertRaises(ProtectedError):
                    record.delete()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FollowUp.objects._create_from_service(
                    lead=self.lead,
                    owner=self.owner,
                    scheduled_at=timezone.now(),
                    status=FollowUp.Status.COMPLETED,
                )

    def test_terminal_followup_cannot_be_changed_or_deleted(self):
        for status, field in (
            (FollowUp.Status.COMPLETED, "completed_at"),
            (FollowUp.Status.CANCELLED, "cancelled_at"),
        ):
            with self.subTest(status=status):
                follow_up = self.make_follow_up(status=status)
                follow_up.notes = "changed after closure"
                with self.assertRaises(ValidationError):
                    follow_up.save()
                with self.assertRaises(ProtectedError):
                    follow_up.delete()
                follow_up.refresh_from_db()
                self.assertIsNotNone(getattr(follow_up, field))

    def test_event_instance_and_queryset_mutations_are_rejected(self):
        follow_up = create_follow_up_service(
            actor=self.owner,
            lead_id=self.lead.pk,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        event = FollowUpEvent.objects.get(follow_up=follow_up)
        event.reason = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            FollowUpEvent.objects.create(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.CREATED,
                actor=self.admin,
                new_scheduled_at=follow_up.scheduled_at,
                new_status=FollowUp.Status.PENDING,
            )
        unsaved_event = FollowUpEvent(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.CREATED,
            actor=self.admin,
            new_scheduled_at=follow_up.scheduled_at,
            new_status=FollowUp.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            unsaved_event.save()
        with self.assertRaises(ValidationError):
            FollowUpEvent.objects.filter(pk=event.pk).update(reason="changed")
        with self.assertRaises(ProtectedError):
            FollowUpEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ProtectedError):
            event.delete()
        with self.assertRaises(ValidationError):
            FollowUpEvent.objects.bulk_create([FollowUpEvent(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.CREATED,
                actor=self.admin,
                new_scheduled_at=follow_up.scheduled_at,
                new_status=FollowUp.Status.PENDING,
            )])
        with self.assertRaises(ValidationError):
            FollowUpEvent.objects.bulk_update([event], ["reason"])

    def test_ordinary_followup_orm_writes_are_rejected(self):
        values = {
            "lead": self.lead,
            "owner": self.owner,
            "scheduled_at": timezone.now() + timedelta(hours=1),
        }
        with self.assertRaises(ValidationError):
            FollowUp.objects.create(**values)

        unsaved = FollowUp(**values)
        with self.assertRaises(ValidationError):
            unsaved.save()

        follow_up = self.make_follow_up()
        follow_up.notes = "ordinary instance save"
        with self.assertRaises(ValidationError):
            follow_up.save()
        with self.assertRaises(ValidationError):
            FollowUp.objects.filter(pk=follow_up.pk).update(notes="queryset update")
        with self.assertRaises(ProtectedError):
            FollowUp.objects.filter(pk=follow_up.pk).delete()
        with self.assertRaises(ProtectedError):
            follow_up.delete()
        with self.assertRaises(ValidationError):
            FollowUp.objects.bulk_create([FollowUp(**values)])
        with self.assertRaises(ValidationError):
            FollowUp.objects.bulk_update([follow_up], ["notes"])

    def test_service_write_authorization_is_cleared_after_database_error(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FollowUp.objects._create_from_service(
                    lead=self.lead,
                    owner=self.owner,
                    scheduled_at=timezone.now(),
                    status="INVALID",
                )
        with self.assertRaises(ValidationError):
            FollowUp.objects.create(
                lead=self.lead,
                owner=self.owner,
                scheduled_at=timezone.now(),
            )

        follow_up = self.make_follow_up()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FollowUpEvent.objects._create_from_service(
                    follow_up=follow_up,
                    event_type="INVALID",
                    actor=self.admin,
                    new_scheduled_at=follow_up.scheduled_at,
                    new_status=FollowUp.Status.PENDING,
                )
        with self.assertRaises(ValidationError):
            FollowUpEvent.objects.create(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.CREATED,
                actor=self.admin,
                new_scheduled_at=follow_up.scheduled_at,
                new_status=FollowUp.Status.PENDING,
            )

    @skipUnless(connection.vendor == "postgresql", "Database trigger test requires PostgreSQL")
    def test_postgresql_trigger_rejects_direct_event_update_and_delete(self):
        follow_up = create_follow_up_service(
            actor=self.owner,
            lead_id=self.lead.pk,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        event = FollowUpEvent.objects.get(follow_up=follow_up)
        table = connection.ops.quote_name(FollowUpEvent._meta.db_table)
        statements = (
            (f"UPDATE {table} SET reason = %s WHERE id = %s", ["mutated", event.pk]),
            (f"DELETE FROM {table} WHERE id = %s", [event.pk]),
        )
        for sql, params in statements:
            with self.subTest(sql=sql.split()[0]):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(sql, params)
        self.assertTrue(FollowUpEvent.objects.filter(pk=event.pk).exists())

    def test_event_idempotency_pair_and_actor_key_unique_constraints(self):
        follow_up = self.make_follow_up()
        key = uuid.uuid4()
        fingerprint = "d" * 64

        for invalid_kwargs in (
            {"idempotency_key": key, "request_fingerprint": None},
            {"idempotency_key": None, "request_fingerprint": fingerprint},
        ):
            with self.subTest(invalid_kwargs=invalid_kwargs):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        FollowUpEvent.objects._create_from_service(
                            follow_up=follow_up,
                            event_type=FollowUpEvent.EventType.RESCHEDULED,
                            actor=self.owner,
                            previous_scheduled_at=follow_up.scheduled_at,
                            new_scheduled_at=follow_up.scheduled_at,
                            previous_status=FollowUp.Status.PENDING,
                            new_status=FollowUp.Status.PENDING,
                            **invalid_kwargs,
                        )

        FollowUpEvent.objects._create_from_service(
            follow_up=follow_up,
            event_type=FollowUpEvent.EventType.RESCHEDULED,
            actor=self.owner,
            previous_scheduled_at=follow_up.scheduled_at,
            new_scheduled_at=follow_up.scheduled_at,
            previous_status=FollowUp.Status.PENDING,
            new_status=FollowUp.Status.PENDING,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FollowUpEvent.objects._create_from_service(
                    follow_up=follow_up,
                    event_type=FollowUpEvent.EventType.RESCHEDULED,
                    actor=self.owner,
                    previous_scheduled_at=follow_up.scheduled_at,
                    new_scheduled_at=follow_up.scheduled_at,
                    previous_status=FollowUp.Status.PENDING,
                    new_status=FollowUp.Status.PENDING,
                    idempotency_key=key,
                    request_fingerprint=fingerprint,
                )
