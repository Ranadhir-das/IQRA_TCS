import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.leads.models import Lead
from apps.leads.services import set_lead_assignment


class CallHistoryAndOutcomeAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.calls_url = reverse("call-submit")
        self.outcomes_url = reverse("call-outcome-list")
        self.employee = User.objects.create(
            username="history_employee",
            role=User.Role.EMPLOYEE,
            first_name="History",
            last_name="Employee",
        )
        self.other_employee = User.objects.create(
            username="history_other_employee",
            role=User.Role.EMPLOYEE,
        )
        self.admin = User.objects.create(
            username="history_admin",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.superuser = User.objects.create(
            username="history_superuser",
            role=User.Role.EMPLOYEE,
            is_staff=True,
            is_superuser=True,
        )
        self.inactive = User.objects.create(
            username="history_inactive",
            role=User.Role.EMPLOYEE,
            is_active=False,
        )
        self.lead = Lead.objects.create(
            name="History Student",
            phone="9876508441",
            assigned_to=self.employee,
        )
        self.other_lead = Lead.objects.create(
            name="Other History Student",
            phone="9876508442",
            assigned_to=self.other_employee,
        )
        self.outcome = CallOutcome.objects.create(
            code="HIST_TEST",
            label="Original outcome",
            display_order=3,
        )
        self.other_outcome = CallOutcome.objects.create(
            code="HIST_OTHER",
            label="Other outcome",
            display_order=1,
        )
        self.inactive_outcome = CallOutcome.objects.create(
            code="HIST_INACTIVE",
            label="Inactive outcome",
            is_active=False,
            display_order=2,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def make_call(
        self,
        *,
        caller=None,
        lead=None,
        outcome=None,
        started_at=None,
        notes="employee note",
    ):
        caller = caller or self.employee
        lead = lead or self.lead
        outcome = outcome or self.outcome
        ended_at = started_at or timezone.now() - timedelta(minutes=1)
        return Call.objects.create(
            lead=lead,
            caller=caller,
            client_event_id=uuid.uuid4(),
            outcome=outcome,
            started_at=ended_at - timedelta(seconds=10),
            ended_at=ended_at,
            duration_seconds=10,
            request_fingerprint="a" * 64,
            employee_notes=notes,
        )

    def test_anonymous_and_inactive_users_are_denied(self):
        self.make_call()
        for url in (self.calls_url, self.outcomes_url):
            self.client.force_authenticate(user=None)
            self.assertEqual(self.client.get(url).status_code, 401)
            self.authenticate(self.inactive)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_employee_lists_only_own_calls_and_cannot_retrieve_guessed_other_call(self):
        own_call = self.make_call()
        other_call = self.make_call(caller=self.other_employee, lead=self.other_lead)
        self.authenticate(self.employee)

        response = self.client.get(self.calls_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["results"]], [own_call.pk])
        self.assertEqual(
            self.client.get(reverse("call-history-detail", args=[other_call.pk])).status_code,
            404,
        )

    def test_admin_and_superuser_see_organization_wide_history(self):
        first = self.make_call()
        second = self.make_call(caller=self.other_employee, lead=self.other_lead)
        for user in (self.admin, self.superuser):
            with self.subTest(user=user.username):
                self.authenticate(user)
                response = self.client.get(self.calls_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {row["id"] for row in response.data["results"]},
                    {first.pk, second.pk},
                )

    def test_call_remains_visible_to_original_caller_after_lead_reassignment(self):
        call = self.make_call()
        set_lead_assignment(self.lead, self.other_employee, self.admin)
        self.authenticate(self.employee)

        response = self.client.get(reverse("call-history-detail", args=[call.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], call.pk)
        self.assertEqual(response.data["caller_username_snapshot"], "history_employee")

    def test_history_response_uses_snapshots_and_omits_sensitive_related_fields(self):
        call = self.make_call()
        self.outcome.label = "Changed later"
        self.outcome.is_active = False
        self.outcome.save(update_fields=["label", "is_active", "updated_at"])
        self.employee.first_name = "Renamed"
        self.employee.save(update_fields=["first_name"])
        self.authenticate(self.employee)

        response = self.client.get(reverse("call-history-detail", args=[call.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome_label_snapshot"], "Original outcome")
        self.assertEqual(response.data["caller_name_snapshot"], "History Employee")
        self.assertEqual(response.data["lead_name"], "History Student")
        self.assertNotIn("phone", response.data)
        self.assertNotIn("internal_notes", response.data)
        self.assertNotIn("request_fingerprint", response.data)
        self.assertNotIn("caller_id", response.data)
        self.assertEqual(response.data["employee_notes"], "employee note")

    def test_history_filters_use_snapshot_values_and_date_bounds(self):
        recent = timezone.now().replace(microsecond=0)
        old = recent - timedelta(days=3)
        first = self.make_call(started_at=recent)
        second = self.make_call(outcome=self.other_outcome, started_at=old)
        self.make_call(caller=self.other_employee, lead=self.other_lead, started_at=recent)
        self.authenticate(self.admin)

        response = self.client.get(
            self.calls_url,
            {
                "lead_id": self.lead.pk,
                "outcome_code": self.outcome.code,
                "started_at_from": (recent - timedelta(hours=1)).isoformat(),
                "started_at_to": (recent + timedelta(hours=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data["results"]], [first.pk])

        response = self.client.get(self.calls_url, {"outcome_code": self.other_outcome.code})
        self.assertEqual([row["id"] for row in response.data["results"]], [second.pk])

    def test_admin_caller_filter_works_and_employee_caller_filter_is_rejected(self):
        own = self.make_call()
        other = self.make_call(caller=self.other_employee, lead=self.other_lead)
        self.authenticate(self.admin)
        response = self.client.get(self.calls_url, {"caller_id": self.other_employee.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["results"]], [other.pk])

        self.authenticate(self.employee)
        response = self.client.get(self.calls_url, {"caller_id": self.other_employee.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn("caller_id", response.data)
        self.assertNotIn(own.pk, [row["id"] for row in response.data.get("results", [])])

    def test_invalid_history_filters_and_arbitrary_ordering_are_rejected(self):
        self.make_call()
        self.authenticate(self.admin)
        invalid_filters = (
            {"lead_id": "abc"},
            {"lead_id": 0},
            {"outcome_code": "lowercase"},
            {"started_at_from": "2026-01-01T12:00:00"},
            {
                "started_at_from": "2026-01-02T00:00:00+00:00",
                "started_at_to": "2026-01-01T00:00:00+00:00",
            },
            {"order_by": "caller__username"},
        )
        for params in invalid_filters:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.calls_url, params).status_code, 400)

    def test_history_pagination_is_stable_and_capped_at_100(self):
        timestamp = timezone.now().replace(microsecond=0)
        calls = [self.make_call(started_at=timestamp) for _ in range(105)]
        self.authenticate(self.admin)

        first = self.client.get(self.calls_url, {"page_size": 1000})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data["results"]), 100)
        self.assertEqual(first.data["count"], 105)
        expected_order = sorted((call.pk for call in calls), reverse=True)
        self.assertEqual([row["id"] for row in first.data["results"]], expected_order[:100])
        second = self.client.get(self.calls_url, {"page_size": 1000, "page": 2})
        self.assertEqual([row["id"] for row in second.data["results"]], expected_order[100:])

    def test_call_history_query_counts_are_bounded(self):
        for _ in range(5):
            self.make_call()
        self.authenticate(self.employee)
        with self.assertNumQueries(2):
            response = self.client.get(self.calls_url)
        self.assertEqual(response.status_code, 200)
        call_id = response.data["results"][0]["id"]
        with self.assertNumQueries(1):
            detail = self.client.get(reverse("call-history-detail", args=[call_id]))
        self.assertEqual(detail.status_code, 200)

    def test_employee_sees_active_outcomes_only_admin_sees_all(self):
        self.authenticate(self.employee)
        response = self.client.get(self.outcomes_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["code"] for row in response.data},
            {self.outcome.code, self.other_outcome.code},
        )
        for user in (self.admin, self.superuser):
            with self.subTest(user=user.username):
                self.authenticate(user)
                response = self.client.get(self.outcomes_url)
                self.assertEqual(
                    {row["code"] for row in response.data},
                    {self.outcome.code, self.other_outcome.code, self.inactive_outcome.code},
                )
                self.assertEqual(
                    [(row["display_order"], row["label"]) for row in response.data],
                    [(1, "Other outcome"), (2, "Inactive outcome"), (3, "Original outcome")],
                )

    def test_outcome_create_rejects_duplicate_and_invalid_codes(self):
        self.authenticate(self.admin)
        payload = {"code": "CALLBACK_X", "label": "Callback", "display_order": 4}
        created = self.client.post(self.outcomes_url, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data["is_active"])
        self.assertEqual(created.data["display_order"], 4)

        duplicate = self.client.post(
            self.outcomes_url,
            {**payload, "label": "Duplicate"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        invalid = self.client.post(
            self.outcomes_url,
            {**payload, "code": "callback-x"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        invalid_order = self.client.post(
            self.outcomes_url,
            {**payload, "code": "CALLBACK_Y", "display_order": 2147483648},
            format="json",
        )
        self.assertEqual(invalid_order.status_code, 400)
        unknown = self.client.post(
            self.outcomes_url,
            {**payload, "created_at": "attacker-value"},
            format="json",
        )
        self.assertEqual(unknown.status_code, 400)

        self.authenticate(self.superuser)
        superuser_created = self.client.post(
            self.outcomes_url,
            {"code": "SUPERUSER_CODE", "label": "Superuser managed", "display_order": 5},
            format="json",
        )
        self.assertEqual(superuser_created.status_code, 201, superuser_created.data)

    def test_employee_cannot_create_or_update_outcomes(self):
        self.authenticate(self.employee)
        response = self.client.post(
            self.outcomes_url,
            {"code": "EMPLOYEE_CODE", "label": "Not allowed", "display_order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.patch(
            reverse("call-outcome-detail", args=[self.outcome.pk]),
            {"label": "Not allowed"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_edit_label_state_order_and_code_is_rejected(self):
        call = self.make_call()
        self.authenticate(self.admin)
        url = reverse("call-outcome-detail", args=[self.outcome.pk])
        updated = self.client.patch(
            url,
            {"label": "Updated", "is_active": False, "display_order": 8},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["label"], "Updated")
        self.assertFalse(updated.data["is_active"])
        self.assertEqual(updated.data["display_order"], 8)

        reactivated = self.client.patch(
            url,
            {"is_active": True},
            format="json",
        )
        self.assertEqual(reactivated.status_code, 200)
        self.assertTrue(reactivated.data["is_active"])

        changed_code = self.client.patch(url, {"code": "NEW_CODE"}, format="json")
        self.assertEqual(changed_code.status_code, 400)
        self.assertIn("immutable", str(changed_code.data["code"]))
        deleted = self.client.delete(url)
        self.assertEqual(deleted.status_code, 405)

        history = self.client.get(reverse("call-history-detail", args=[call.pk]))
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data["outcome_code_snapshot"], "HIST_TEST")
        self.assertEqual(history.data["outcome_label_snapshot"], "Original outcome")

    def test_outcome_list_query_count_is_constant(self):
        for index in range(10):
            CallOutcome.objects.create(
                code=f"MORE_{index}",
                label=f"More {index}",
                display_order=index + 10,
            )
        self.authenticate(self.employee)
        with self.assertNumQueries(1):
            response = self.client.get(self.outcomes_url)
        self.assertEqual(response.status_code, 200)

    def test_admin_call_history_includes_caller_id_and_rejects_repeated_pagination_params(self):
        call = self.make_call()

        # Employee representation omits caller_id
        self.authenticate(self.employee)
        emp_list = self.client.get(self.calls_url)
        self.assertEqual(emp_list.status_code, 200)
        self.assertNotIn("caller_id", emp_list.data["results"][0])

        # Administrator representation includes caller_id on both list and detail
        for admin_like in (self.admin, self.superuser):
            with self.subTest(admin=admin_like.username):
                self.authenticate(admin_like)
                admin_list = self.client.get(self.calls_url)
                self.assertEqual(admin_list.status_code, 200)
                self.assertEqual(
                    admin_list.data["results"][0]["caller_id"],
                    self.employee.pk,
                )
                admin_detail = self.client.get(
                    reverse("call-history-detail", args=[call.pk])
                )
                self.assertEqual(admin_detail.status_code, 200)
                self.assertEqual(admin_detail.data["caller_id"], self.employee.pk)

        # Repeated or invalid pagination parameters return 400
        self.authenticate(self.admin)
        for bad_query in (
            "page_size=0",
            "page_size=abc",
            "page_size=10&page_size=20",
            "page=1&page=2",
        ):
            with self.subTest(query=bad_query):
                resp = self.client.get(f"{self.calls_url}?{bad_query}")
                self.assertEqual(resp.status_code, 400)
