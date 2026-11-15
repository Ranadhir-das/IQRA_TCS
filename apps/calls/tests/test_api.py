import uuid
from datetime import timedelta, timezone

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.services import set_lead_assignment


class CallSubmissionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("call-submit")
        self.employee = User.objects.create_user(
            username="api_call_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            first_name="API",
            last_name="Employee",
        )
        self.other_employee = User.objects.create_user(
            username="api_call_other",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
        )
        self.admin_user = User.objects.create_user(
            username="api_call_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
        )
        self.lead = Lead.objects.create(
            name="API Student",
            phone="9876508331",
            assigned_to=self.employee,
            internal_notes="secret admin note",
        )
        self.other_lead = Lead.objects.create(
            name="Other API Student",
            phone="9876508332",
            assigned_to=self.other_employee,
        )
        self.outcome, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER", defaults={"label": "No answer"}
        )
        self.alternate_outcome, _ = CallOutcome.objects.get_or_create(
            code="INTERESTED", defaults={"label": "Interested"}
        )

    def payload(self, *, lead=None, event_id=None, notes="Followed up"):
        ended = django_timezone.now().astimezone(timezone.utc).replace(microsecond=123456)
        return {
            "lead_id": (lead or self.lead).pk,
            "client_event_id": str(event_id or uuid.uuid4()),
            "outcome_code": self.outcome.code,
            "started_at": (ended - timedelta(seconds=8, microseconds=700000)).isoformat(),
            "ended_at": ended.isoformat(),
            "employee_notes": notes,
        }

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.employee)

    def test_anonymous_request_is_rejected(self):
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 401)

    def test_active_employee_submits_and_receives_server_generated_values(self):
        self.authenticate()
        before_history = LeadAssignmentHistory.objects.count()
        response = self.client.post(self.url, self.payload(notes="  hello\r\nworld  "), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        call = Call.objects.get()
        self.assertEqual(response.data["id"], call.pk)
        self.assertEqual(response.data["duration_seconds"], 8)
        self.assertEqual(response.data["employee_notes"], "hello\nworld")
        self.assertEqual(call.caller_id, self.employee.pk)
        self.assertEqual(call.caller_username_snapshot, self.employee.username)
        self.assertEqual(call.caller_name_snapshot, "API Employee")
        self.assertEqual(call.outcome_code_snapshot, self.outcome.code)
        self.assertEqual(call.outcome_label_snapshot, self.outcome.label)
        self.assertNotIn("request_fingerprint", response.data)
        self.assertNotIn("caller", response.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertEqual(self.lead.assigned_to_id, self.employee.pk)
        self.assertEqual(LeadAssignmentHistory.objects.count(), before_history)

    def test_inactive_employee_admin_and_employee_role_superuser_are_denied(self):
        inactive = User.objects.create_user(
            username="api_call_inactive",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            is_active=False,
        )
        super_employee = User.objects.create_user(
            username="api_call_super_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            is_superuser=True,
        )
        for user in (inactive, self.admin_user, super_employee):
            with self.subTest(user=user.username):
                self.authenticate(user)
                response = self.client.post(self.url, self.payload(), format="json")
                self.assertEqual(response.status_code, 403)
        self.assertEqual(Call.objects.count(), 0)

    def test_unassigned_other_employee_and_archived_leads_have_generic_404(self):
        unassigned = Lead.objects.create(name="Unassigned API", phone="9876508333")
        archived = Lead.objects.create(
            name="Archived API",
            phone="9876508334",
            assigned_to=self.employee,
            is_archived=True,
        )
        self.authenticate()
        for lead in (unassigned, self.other_lead, archived):
            with self.subTest(lead_id=lead.pk):
                response = self.client.post(self.url, self.payload(lead=lead), format="json")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(str(response.data["detail"]), "Lead is not available for this call.")
        self.assertEqual(Call.objects.count(), 0)

    def test_reassignment_and_archive_before_submission_are_enforced(self):
        actor = User.objects.create_user(
            username="api_call_assignment_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
        )
        set_lead_assignment(self.lead, self.other_employee, actor)
        reassigned = Lead.objects.create(
            name="Will Be Archived",
            phone="9876508335",
            assigned_to=self.employee,
        )
        reassigned.is_archived = True
        reassigned.save(update_fields=["is_archived", "updated_at"])
        self.authenticate()
        for lead in (self.lead, reassigned):
            with self.subTest(lead_id=lead.pk):
                response = self.client.post(self.url, self.payload(lead=lead), format="json")
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Call.objects.count(), 0)

    def test_inactive_outcome_is_rejected_before_new_submission(self):
        self.authenticate()
        self.outcome.is_active = False
        self.outcome.save(update_fields=["is_active", "updated_at"])
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("outcome_code", response.data)
        self.assertEqual(Call.objects.count(), 0)

    def test_active_outcome_uses_current_label_snapshot(self):
        self.authenticate()
        self.outcome.label = "Updated label"
        self.outcome.save(update_fields=["label", "updated_at"])
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["outcome_code_snapshot"], self.outcome.code)
        self.assertEqual(response.data["outcome_label_snapshot"], "Updated label")

    def test_exact_retry_returns_same_call_even_after_outcome_deactivation_and_rename(self):
        self.authenticate()
        payload = self.payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)

        self.outcome.label = "Retired label"
        self.outcome.is_active = False
        self.outcome.save(update_fields=["label", "is_active", "updated_at"])
        set_lead_assignment(self.lead, self.other_employee, self.admin_user)
        self.lead.is_archived = True
        self.lead.save(update_fields=["is_archived", "updated_at"])

        retry = self.client.post(self.url, payload, format="json")
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data["id"], first.data["id"])
        self.assertEqual(retry.data["outcome_label_snapshot"], "No answer")
        self.assertEqual(Call.objects.count(), 1)

    def test_same_uuid_is_independent_between_callers(self):
        event_id = uuid.uuid4()
        first_payload = self.payload(event_id=event_id)
        self.authenticate(self.employee)
        first = self.client.post(self.url, first_payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)

        self.authenticate(self.other_employee)
        second_payload = self.payload(lead=self.other_lead, event_id=event_id)
        second = self.client.post(self.url, second_payload, format="json")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(Call.objects.filter(client_event_id=event_id).count(), 2)

    def test_conflicting_payload_changes_return_409(self):
        self.authenticate()
        payload = self.payload()
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 201)
        for changed in (
            {"lead_id": self.other_lead.pk},
            {"ended_at": (django_timezone.now() + timedelta(seconds=2)).astimezone(timezone.utc).isoformat()},
            {"outcome_code": self.alternate_outcome.code},
            {"employee_notes": "different"},
        ):
            with self.subTest(changed=changed):
                response = self.client.post(self.url, {**payload, **changed}, format="json")
                self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(Call.objects.count(), 1)

    def test_timestamp_validation_and_explicit_offsets(self):
        self.authenticate()
        payload = self.payload()
        naive = payload["started_at"].replace("+00:00", "")
        response = self.client.post(self.url, {**payload, "started_at": naive}, format="json")
        self.assertEqual(response.status_code, 400)

        now = django_timezone.now().astimezone(timezone.utc).replace(microsecond=0)
        yesterday = now - timedelta(days=1)
        for start, end, expected in (
            (now, now, 201),
            (yesterday, now, 201),
            (yesterday, now + timedelta(microseconds=1), 400),
            (now + timedelta(minutes=4), now + timedelta(minutes=4, seconds=1), 201),
            (now + timedelta(minutes=6), now + timedelta(minutes=6, seconds=1), 400),
            (now, now - timedelta(microseconds=1), 400),
        ):
            with self.subTest(start=start, end=end):
                body = {
                    **self.payload(),
                    "started_at": start.isoformat(),
                    "ended_at": end.isoformat(),
                }
                response = self.client.post(self.url, body, format="json")
                self.assertEqual(response.status_code, expected, response.data)

    def test_null_and_empty_notes_normalize_to_same_retry(self):
        self.authenticate()
        payload = self.payload(notes=None)
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        retry = self.client.post(self.url, {**payload, "employee_notes": ""}, format="json")
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data["id"], retry.data["id"])

    def test_unknown_and_server_managed_fields_are_rejected(self):
        self.authenticate()
        forbidden = (
            "caller",
            "caller_username_snapshot",
            "outcome_label_snapshot",
            "duration_seconds",
            "request_fingerprint",
            "created_at",
            "outcome_label",
        )
        for field in forbidden:
            with self.subTest(field=field):
                response = self.client.post(
                    self.url,
                    {**self.payload(), field: "attacker-value"},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.assertEqual(Call.objects.count(), 0)
