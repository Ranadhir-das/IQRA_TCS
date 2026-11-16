import threading
import uuid
from datetime import timedelta, timezone
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from django.utils import timezone as django_timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.calls.services import CallEventConflict, submit_call
from apps.leads.models import Lead
from apps.leads.services import set_lead_assignment


def call_values(*, caller, lead, event_id=None, outcome_code="NO_ANSWER", notes=""):
    ended_at = django_timezone.now().astimezone(timezone.utc)
    return {
        "caller": caller,
        "lead_id": lead.pk,
        "client_event_id": event_id or uuid.uuid4(),
        "outcome_code": outcome_code,
        "started_at": ended_at - timedelta(seconds=3, microseconds=900000),
        "ended_at": ended_at,
        "employee_notes": notes,
    }


class CallSubmissionServiceTests(TransactionTestCase):
    def setUp(self):
        self.employee = User.objects.create_user(
            username="service_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
        )
        self.other_employee = User.objects.create_user(
            username="service_other_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
        )
        self.lead = Lead.objects.create(
            name="Service Student",
            phone="9876508111",
            assigned_to=self.employee,
        )
        self.other_lead = Lead.objects.create(
            name="Other Service Student",
            phone="9876508112",
            assigned_to=self.other_employee,
        )
        self.outcome, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER", defaults={"label": "No answer"}
        )
        self.alternate_outcome, _ = CallOutcome.objects.get_or_create(
            code="INTERESTED", defaults={"label": "Interested"}
        )

    def test_service_uses_database_objects_and_creates_server_values(self):
        values = call_values(caller=self.employee, lead=self.lead)
        call, created = submit_call(**values)
        self.assertTrue(created)
        self.assertEqual(call.caller_id, self.employee.pk)
        self.assertEqual(call.lead_id, self.lead.pk)
        self.assertEqual(call.outcome, self.outcome)
        self.assertEqual(call.duration_seconds, 3)
        self.assertEqual(call.employee_notes, "")
        self.assertEqual(call.caller_username_snapshot, self.employee.username)
        self.assertEqual(call.outcome_code_snapshot, self.outcome.code)
        self.assertEqual(len(call.request_fingerprint), 64)

    def test_ineligible_leads_and_outcomes_are_rejected(self):
        unassigned = Lead.objects.create(name="Unassigned", phone="9876508113")
        archived = Lead.objects.create(
            name="Archived", phone="9876508114", assigned_to=self.employee, is_archived=True
        )
        for lead in (unassigned, self.other_lead, archived):
            with self.subTest(lead=lead.pk):
                with self.assertRaises(NotFound):
                    submit_call(**call_values(caller=self.employee, lead=lead))

        self.outcome.is_active = False
        self.outcome.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(ValidationError):
            submit_call(**call_values(caller=self.employee, lead=self.lead))

    def test_locked_caller_is_revalidated_as_active_non_superuser_employee(self):
        values = call_values(caller=self.employee, lead=self.lead)
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            submit_call(**values)

    def test_exact_retry_is_returned_after_assignment_archive_and_outcome_changes(self):
        values = call_values(caller=self.employee, lead=self.lead, notes="call me")
        original, created = submit_call(**values)
        self.assertTrue(created)

        actor = User.objects.create_user(
            username="service_retry_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
        )
        set_lead_assignment(self.lead, self.other_employee, actor)
        self.lead.refresh_from_db()
        self.lead.is_archived = True
        self.lead.save(update_fields=["is_archived", "updated_at"])
        self.outcome.label = "Retired label"
        self.outcome.is_active = False
        self.outcome.save(update_fields=["label", "is_active", "updated_at"])

        retry, retry_created = submit_call(**values)
        self.assertFalse(retry_created)
        self.assertEqual(retry.pk, original.pk)
        self.assertEqual(retry.outcome_label_snapshot, "No answer")
        self.assertEqual(Call.objects.count(), 1)

    def test_assignment_to_caller_after_reassignment_allows_new_submission(self):
        actor = User.objects.create_user(
            username="service_assignment_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
        )
        set_lead_assignment(self.lead, self.other_employee, actor)
        with self.assertRaises(NotFound):
            submit_call(**call_values(caller=self.employee, lead=self.lead))
        set_lead_assignment(self.lead, self.employee, actor)
        call, created = submit_call(**call_values(caller=self.employee, lead=self.lead))
        self.assertTrue(created)
        self.assertEqual(call.lead_id, self.lead.pk)

    def test_same_event_different_canonical_payload_conflicts(self):
        values = call_values(caller=self.employee, lead=self.lead)
        submit_call(**values)
        for changed in (
            {"lead_id": self.other_lead.pk},
            {"ended_at": values["ended_at"] + timedelta(seconds=1)},
            {"outcome_code": self.alternate_outcome.code},
            {"employee_notes": "changed"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(CallEventConflict):
                    submit_call(**{**values, **changed})
        self.assertEqual(Call.objects.count(), 1)

    def test_timestamp_business_rules_and_flooring(self):
        now = django_timezone.now().astimezone(timezone.utc)
        values = call_values(caller=self.employee, lead=self.lead)

        with self.assertRaises(ValidationError):
            submit_call(**{
                **values,
                "started_at": now,
                "ended_at": now - timedelta(microseconds=1),
            })
        with self.assertRaises(ValidationError):
            submit_call(**{
                **values,
                "started_at": now,
                "ended_at": now + timedelta(days=1, microseconds=1),
            })
        with self.assertRaises(ValidationError):
            submit_call(**{
                **values,
                "started_at": now + timedelta(minutes=6),
                "ended_at": now + timedelta(minutes=6, seconds=1),
            })

        call, _ = submit_call(**{
            **values,
            "started_at": now,
            "ended_at": now + timedelta(seconds=1, microseconds=999999),
        })
        self.assertEqual(call.duration_seconds, 1)


@skipUnless(connection.vendor == "postgresql", "Concurrent call submission requires PostgreSQL")
class ConcurrentCallSubmissionTests(TransactionTestCase):
    def setUp(self):
        self.employee = User.objects.create_user(
            username="concurrent_call_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
        )
        self.lead = Lead.objects.create(
            name="Concurrent Student",
            phone="9876508221",
            assigned_to=self.employee,
        )
        self.outcome, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER", defaults={"label": "No answer"}
        )

    def run_concurrent_submissions(self, *, conflicting=False):
        values = call_values(caller=self.employee, lead=self.lead, notes="first")
        second_values = {**values, "employee_notes": "different"} if conflicting else dict(values)

        begin_lock = threading.Barrier(2)
        both_at_lock_query = threading.Event()
        first_lock_acquired = threading.Event()
        release_first = threading.Event()
        guard = threading.Lock()
        entered_threads = set()
        returned_threads = []
        thread_local = threading.local()
        results = {}
        failures = {}
        database_connections = {}
        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            name = threading.current_thread().name
            is_claim = (
                queryset.model is Lead
                and queryset.query.select_for_update
                and not getattr(thread_local, "claim_seen", False)
            )
            if not is_claim:
                return original_get(queryset, *args, **kwargs)

            begin_lock.wait(timeout=5)
            with guard:
                entered_threads.add(name)
                if len(entered_threads) == 2:
                    both_at_lock_query.set()

            lead = original_get(queryset, *args, **kwargs)
            with guard:
                returned_threads.append(name)
                first = not first_lock_acquired.is_set()
                if first:
                    first_lock_acquired.set()
            thread_local.claim_seen = True
            if first and not release_first.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release the first lead lock")
            return lead

        def worker(label, payload):
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                database_connections[label] = connections["default"].connection
                results[label] = submit_call(**payload)
            except CallEventConflict as exc:
                results[label] = exc
            except BaseException as exc:
                failures[label] = exc
            finally:
                connections.close_all()

        workers = [
            threading.Thread(
                target=worker,
                args=(label, payload),
                name=label,
                daemon=True,
            )
            for label, payload in (
                ("call-submit-1", values),
                ("call-submit-2", second_values),
            )
        ]
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                for thread in workers:
                    thread.start()
                self.assertTrue(first_lock_acquired.wait(timeout=5))
                self.assertTrue(both_at_lock_query.wait(timeout=5))
                # Keep the first transaction's Lead lock while the independent
                # second connection issues its real SELECT FOR UPDATE query.
                self.assertFalse(release_first.wait(timeout=0.2))
                self.assertEqual(len(returned_threads), 1)
        finally:
            release_first.set()
            for thread in workers:
                if thread.ident is not None:
                    thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertFalse(failures, f"Unexpected worker errors: {failures!r}")
        self.assertEqual(len(database_connections), 2)
        self.assertIsNot(
            database_connections["call-submit-1"],
            database_connections["call-submit-2"],
        )
        return results

    def test_concurrent_identical_submissions_create_one_call(self):
        results = self.run_concurrent_submissions()
        self.assertEqual(len(results), 2)
        calls = [call for call, _created in results.values()]
        created_flags = [created for _call, created in results.values()]
        self.assertCountEqual(created_flags, [True, False])
        self.assertEqual(calls[0].pk, calls[1].pk)
        self.assertEqual(Call.objects.count(), 1)

    def test_concurrent_conflicting_submissions_return_one_conflict(self):
        results = self.run_concurrent_submissions(conflicting=True)
        self.assertEqual(sum(isinstance(value, CallEventConflict) for value in results.values()), 1)
        self.assertEqual(sum(isinstance(value, tuple) for value in results.values()), 1)
        self.assertEqual(Call.objects.count(), 1)
