import uuid
from importlib import import_module
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.db.migrations import RunPython
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.admin import CallAdmin, CallOutcomeAdmin
from apps.calls.models import Call, CallOutcome
from apps.leads.models import Lead


class CallModelTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(
            username="call_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            first_name="Call",
            last_name="Employee",
        )
        self.lead = Lead.objects.create(name="Call Student", phone="9876507777")
        self.outcome, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER", defaults={"label": "No answer"}
        )

    def make_call(self, *, event_id=None, started_at=None, ended_at=None, duration=90):
        ended_at = ended_at or timezone.now()
        started_at = started_at or ended_at - timedelta(seconds=duration)
        return Call.objects.create(
            lead=self.lead,
            caller=self.caller,
            client_event_id=event_id or uuid.uuid4(),
            outcome=self.outcome,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            request_fingerprint="a" * 64,
        )

    def test_default_outcomes_are_seeded_and_configurable(self):
        # Exercise the migration function directly so this test also works on
        # an existing --keepdb database whose migration has already run but
        # whose rows were removed by the previous test-suite flush.
        CallOutcome.objects.all().delete()
        seed_migration = import_module(
            "apps.calls.migrations.0002_seed_default_outcomes"
        )
        seed_migration.seed_default_outcomes(
            django_apps, SimpleNamespace(connection=connection)
        )
        expected = {
            "NO_ANSWER": "No answer",
            "CALLBACK_REQUESTED": "Callback requested",
            "INTERESTED": "Interested",
            "NOT_INTERESTED": "Not interested",
            "INVALID_NUMBER": "Invalid number",
        }
        actual = dict(
            CallOutcome.objects.filter(code__in=expected).values_list("code", "label")
        )
        self.assertEqual(actual, expected)
        self.assertTrue(all(
            CallOutcome.objects.get(code=code).is_active for code in expected
        ))
        self.assertFalse(CallOutcome.objects.filter(code="CONNECTED").exists())
        outcome = CallOutcome.objects.get(code="NO_ANSWER")
        outcome.label = "Configured label"
        outcome.is_active = False
        outcome.save(update_fields=["label", "is_active", "updated_at"])
        seed_migration.seed_default_outcomes(
            django_apps, SimpleNamespace(connection=connection)
        )
        outcome.refresh_from_db()
        self.assertEqual(outcome.label, "Configured label")
        self.assertFalse(outcome.is_active)
        self.assertIs(
            seed_migration.Migration.operations[0].reverse_code,
            RunPython.noop,
        )

    def test_outcome_codes_are_unique_and_immutable_but_labels_and_active_state_change(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CallOutcome.objects.create(code="NO_ANSWER", label="Duplicate")

        original_code = self.outcome.code
        self.outcome.code = "NO_REPLY"
        with self.assertRaises(ValidationError):
            self.outcome.save()

        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.code, original_code)
        self.outcome.label = "No response"
        self.outcome.is_active = False
        self.outcome.save(update_fields=["label", "is_active", "updated_at"])
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.label, "No response")
        self.assertFalse(self.outcome.is_active)

    @skipUnless(connection.vendor == "postgresql", "Database trigger test requires PostgreSQL")
    def test_postgresql_trigger_rejects_queryset_code_updates(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CallOutcome.objects.filter(pk=self.outcome.pk).update(code="NO_REPLY")
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.code, "NO_ANSWER")

    def test_outcome_code_format_is_database_constrained(self):
        for invalid_code in ("lowercase", "NO_ANSWER\n"):
            with self.subTest(code=invalid_code):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        CallOutcome.objects.create(code=invalid_code, label="Invalid")

    def test_call_snapshots_preserve_original_outcome_and_caller_identity(self):
        call = self.make_call()
        self.assertEqual(call.outcome_code_snapshot, "NO_ANSWER")
        self.assertEqual(call.outcome_label_snapshot, "No answer")
        self.assertEqual(call.caller_username_snapshot, "call_employee")
        self.assertEqual(call.caller_name_snapshot, "Call Employee")

        self.outcome.label = "No response"
        self.outcome.save(update_fields=["label", "updated_at"])
        self.caller.first_name = "Renamed"
        self.caller.is_active = False
        self.caller.save(update_fields=["first_name", "is_active"])

        call.refresh_from_db()
        self.assertEqual(call.outcome.label, "No response")
        self.assertEqual(call.caller.first_name, "Renamed")
        self.assertFalse(call.caller.is_active)
        self.assertTrue(Call.objects.filter(pk=call.pk).exists())
        self.assertEqual(call.outcome_label_snapshot, "No answer")
        self.assertEqual(call.caller_name_snapshot, "Call Employee")

    def test_call_snapshots_cannot_be_changed_by_model_updates(self):
        call = self.make_call()
        call.outcome_label_snapshot = "Changed historical label"
        with self.assertRaises(ValidationError):
            call.save()
        call.refresh_from_db()
        self.assertEqual(call.outcome_label_snapshot, "No answer")

    def test_call_event_is_unique_per_caller(self):
        event_id = uuid.uuid4()
        self.make_call(event_id=event_id)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_call(event_id=event_id)

    def test_call_timestamps_must_be_ordered(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_call(
                    started_at=now,
                    ended_at=now - timedelta(seconds=1),
                )

    def test_call_duration_is_bounded_from_zero_to_one_day(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_call(duration=86401)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Call.objects.create(
                    lead=self.lead,
                    caller=self.caller,
                    client_event_id=uuid.uuid4(),
                    outcome=self.outcome,
                    started_at=timezone.now(),
                    ended_at=timezone.now(),
                    duration_seconds=-1,
                    request_fingerprint="b" * 64,
                )

    def test_deleting_referenced_lead_caller_or_outcome_is_protected(self):
        self.make_call()
        for record in (self.lead, self.caller, self.outcome):
            with self.subTest(model=type(record).__name__):
                with self.assertRaises(ProtectedError):
                    record.delete()


class CallAdminTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/calls/")
        self.request.user = User.objects.create_superuser(
            username="call_admin",
            email="call-admin@example.test",
            password="SecureTestPass@123",
        )
        self.site = AdminSite()

    def test_call_outcome_admin_locks_code_after_creation(self):
        outcome, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER", defaults={"label": "No answer"}
        )
        model_admin = CallOutcomeAdmin(CallOutcome, self.site)
        readonly = model_admin.get_readonly_fields(self.request, outcome)
        self.assertIn("code", readonly)
        self.assertNotIn("label", readonly)
        self.assertNotIn("is_active", readonly)
        self.assertIn("code", model_admin.search_fields)
        self.assertIn("label", model_admin.search_fields)
        self.assertIn("is_active", model_admin.list_filter)

    def test_call_admin_is_read_only_and_does_not_list_contact_details(self):
        model_admin = CallAdmin(Call, self.site)
        self.assertFalse(model_admin.has_add_permission(self.request))
        self.assertFalse(model_admin.has_change_permission(self.request))
        self.assertFalse(model_admin.has_delete_permission(self.request))
        self.assertIn("lead", model_admin.list_select_related)
        self.assertIn("caller", model_admin.list_select_related)
        self.assertNotIn("phone", model_admin.list_display)
        self.assertNotIn("email", model_admin.list_display)
        self.assertIn(Call, admin.site._registry)
        self.assertIn(CallOutcome, admin.site._registry)
