from unittest.mock import patch
from types import SimpleNamespace

from django.contrib import admin
from rest_framework import status
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.test.client import RequestFactory

from apps.accounts.models import User
from apps.leads.admin import LeadAdmin
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.services import (
    set_lead_assignment,
    set_lead_assignment as real_set_lead_assignment,
)


LEADS_URL = "/api/v1/leads/"
BULK_ASSIGN_URL = f"{LEADS_URL}bulk-assign/"


def _make_user(username, role):
    user = User.objects.create_user(
        username=username,
        password="SecureTestPass@123",
        email=f"{username}@iqracentral.com",
        role=role,
        is_staff=(role == User.Role.ADMIN),
    )
    return user


def _make_lead(name, phone, **kwargs):
    return Lead.objects.create(name=name, phone=phone, **kwargs)


class LeadAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("lead_admin", User.Role.ADMIN)
        self.employee = _make_user("lead_employee", User.Role.EMPLOYEE)
        self.other_employee = _make_user("other_employee", User.Role.EMPLOYEE)

        self.assigned_lead = _make_lead(
            "Assigned Student", "9876500001", assigned_to=self.employee
        )
        self.other_lead = _make_lead(
            "Other Student", "9876500002", assigned_to=self.other_employee
        )
        self.unassigned_lead = _make_lead("Unassigned Student", "9876500003")
        self.archived_lead = _make_lead(
            "Archived Student",
            "9876500004",
            assigned_to=self.employee,
            is_archived=True,
        )

    def authenticate(self, user):
        access = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_admin_can_create_and_retrieve_lead(self):
        self.authenticate(self.admin)
        create_response = self.client.post(
            LEADS_URL,
            {
                "name": "New Enquiry",
                "phone": "+91 98765 43210",
                "alternate_phone": "9876500010",
                "email": "new@example.com",
                "course_interest": "BBA",
                "enquiry_details": "Asked about the next intake.",
                "assigned_to": self.employee.pk,
                "assignment_reason": "Initial enquiry assignment",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        lead_id = create_response.json()["id"]
        created = Lead.objects.get(pk=lead_id)
        self.assertEqual(created.created_by, self.admin)
        self.assertEqual(created.assigned_to, self.employee)
        self.assertEqual(created.status, Lead.Status.NEW)
        history = LeadAssignmentHistory.objects.get(lead=created)
        self.assertIsNone(history.previous_employee)
        self.assertEqual(history.new_employee, self.employee)
        self.assertEqual(history.changed_by, self.admin)
        self.assertEqual(history.reason, "Initial enquiry assignment")
        self.assertIsNotNone(history.changed_at)
        self.assertEqual(history.new_employee_username, self.employee.username)
        self.assertEqual(
            history.new_employee_name,
            self.employee.get_full_name() or self.employee.username,
        )
        self.assertEqual(history.changed_by_username, self.admin.username)
        self.assertEqual(
            history.changed_by_name,
            self.admin.get_full_name() or self.admin.username,
        )

        event_response = self.client.get(
            f"{LEADS_URL}{lead_id}/assignment-history/"
        )
        event = event_response.json()["results"][0]
        self.assertEqual(event["new_employee"]["username"], history.new_employee_username)
        self.assertEqual(event["changed_by"]["full_name"], history.changed_by_name)

        retrieve_response = self.client.get(f"{LEADS_URL}{lead_id}/")
        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(retrieve_response.json()["name"], "New Enquiry")

    def test_initial_unassigned_lead_creates_no_history(self):
        self.authenticate(self.admin)
        response = self.client.post(
            LEADS_URL,
            {"name": "Unassigned Enquiry", "phone": "9876500017"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(
            LeadAssignmentHistory.objects.filter(lead_id=response.json()["id"]).exists()
        )

    def test_admin_cannot_assign_active_superuser_with_employee_role(self):
        superuser = _make_user("employee_superuser", User.Role.EMPLOYEE)
        superuser.is_superuser = True
        superuser.is_staff = True
        superuser.save(update_fields=["is_superuser", "is_staff"])

        self.authenticate(self.admin)
        response = self.client.post(
            LEADS_URL,
            {"name": "Superuser Assignment", "phone": "9876500012", "assigned_to": superuser.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_assign_inactive_employee(self):
        self.other_employee.is_active = False
        self.other_employee.save(update_fields=["is_active"])
        self.authenticate(self.admin)
        response = self.client.post(
            LEADS_URL,
            {"name": "Inactive Assignment", "phone": "9876500013", "assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_assign_admin_role_user(self):
        self.authenticate(self.admin)
        response = self.client.post(
            LEADS_URL,
            {"name": "Admin Assignment", "phone": "9876500014", "assigned_to": self.admin.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_model_validation_rejects_ineligible_assignees(self):
        superuser = _make_user("model_superuser", User.Role.EMPLOYEE)
        superuser.is_superuser = True
        superuser.save(update_fields=["is_superuser"])
        inactive_employee = _make_user("model_inactive", User.Role.EMPLOYEE)
        inactive_employee.is_active = False
        inactive_employee.save(update_fields=["is_active"])

        for assignee in (self.admin, superuser, inactive_employee):
            with self.subTest(assignee=assignee.username):
                lead = Lead(name="Validation", phone="9876500015", assigned_to=assignee)
                with self.assertRaises(ValidationError):
                    lead.full_clean()

        valid_lead = Lead(
            name="Validation", phone="9876500016", assigned_to=self.employee
        )
        valid_lead.full_clean()

    def test_whitespace_only_phone_is_rejected_by_database_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Lead.objects.create(name="Whitespace phone", phone=" \t\n ")

    def test_invalid_status_is_rejected_by_api(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"status": "NOT_A_STATUS"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_multiple_enquiries_may_share_a_phone_number(self):
        first = _make_lead("First Enquiry", "9876500099")
        second = _make_lead("Second Enquiry", "9876500099")
        self.assertNotEqual(first.pk, second.pk)

    def test_admin_can_update_lead(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {
                "status": Lead.Status.INTERESTED,
                "internal_notes": "Call after 5 PM.",
                "assigned_to": self.other_employee.pk,
                "assignment_reason": "Employee requested reassignment",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.status, Lead.Status.INTERESTED)
        self.assertEqual(self.assigned_lead.internal_notes, "Call after 5 PM.")
        self.assertEqual(self.assigned_lead.assigned_to, self.other_employee)
        history = LeadAssignmentHistory.objects.get(lead=self.assigned_lead)
        self.assertEqual(history.previous_employee, self.employee)
        self.assertEqual(history.new_employee, self.other_employee)
        self.assertEqual(history.changed_by, self.admin)
        self.assertEqual(history.reason, "Employee requested reassignment")
        self.assertIsNotNone(history.changed_at)
        self.assertEqual(history.previous_employee_username, self.employee.username)
        self.assertEqual(history.new_employee_username, self.other_employee.username)
        self.assertEqual(history.changed_by_username, self.admin.username)

    def test_admin_can_explicitly_unassign_lead(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": None, "assignment_reason": "Unassigned by admin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assigned_lead.refresh_from_db()
        self.assertIsNone(self.assigned_lead.assigned_to)
        history = LeadAssignmentHistory.objects.get(lead=self.assigned_lead)
        self.assertEqual(history.previous_employee, self.employee)
        self.assertIsNone(history.new_employee)
        self.assertEqual(history.changed_by, self.admin)
        self.assertEqual(history.reason, "Unassigned by admin")
        self.assertEqual(history.previous_employee_username, self.employee.username)
        self.assertIsNone(history.new_employee_username)

    def test_no_op_assignment_does_not_create_history(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": self.employee.pk, "assignment_reason": "No change"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            LeadAssignmentHistory.objects.filter(lead=self.assigned_lead).exists()
        )

    def test_admin_can_read_history_after_employee_deletion(self):
        self.authenticate(self.admin)
        update = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        set_lead_assignment(
            self.archived_lead, None, self.admin, "Prepare historical employee deletion"
        )
        self.employee.delete()

        response = self.client.get(
            f"{LEADS_URL}{self.assigned_lead.pk}/assignment-history/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = response.json()["results"][0]
        self.assertEqual(event["previous_employee"]["username"], self.employee.username)
        self.assertIsNone(event["previous_employee"]["id"])
        self.assertEqual(event["new_employee"]["id"], self.other_employee.pk)
        self.assertEqual(event["changed_by"]["id"], self.admin.pk)
        self.assertNotIn("email", event["changed_by"])

    def test_employee_and_admin_renames_do_not_rewrite_history(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        history = LeadAssignmentHistory.objects.get(lead=self.assigned_lead)
        employee_username = history.previous_employee_username
        employee_name = history.previous_employee_name
        actor_username = history.changed_by_username
        actor_name = history.changed_by_name

        self.employee.username = "renamed_employee"
        self.employee.first_name = "Changed"
        self.employee.last_name = "Name"
        self.employee.save(update_fields=["username", "first_name", "last_name"])
        self.admin.username = "renamed_admin"
        self.admin.first_name = "Updated"
        self.admin.last_name = "Admin"
        self.admin.save(update_fields=["username", "first_name", "last_name"])

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {str(RefreshToken.for_user(self.admin).access_token)}"
        )
        event_response = self.client.get(
            f"{LEADS_URL}{self.assigned_lead.pk}/assignment-history/"
        )
        event = event_response.json()["results"][0]
        self.assertEqual(event["previous_employee"]["username"], employee_username)
        self.assertEqual(event["previous_employee"]["full_name"], employee_name)
        self.assertEqual(event["changed_by"]["username"], actor_username)
        self.assertEqual(event["changed_by"]["full_name"], actor_name)

    def test_assigned_employee_deletion_is_blocked(self):
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.employee.delete()
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)

    def test_deactivated_employee_can_be_unassigned_and_deleted_with_history_retained(self):
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)

        set_lead_assignment(
            self.assigned_lead,
            None,
            self.admin,
            "Explicitly unassigned before deletion",
        )
        set_lead_assignment(
            self.archived_lead, None, self.admin, "Explicitly unassigned before deletion"
        )
        self.employee.delete()

        self.assigned_lead.refresh_from_db()
        self.assertIsNone(self.assigned_lead.assigned_to)
        history = LeadAssignmentHistory.objects.get(lead=self.assigned_lead)
        self.assertIsNone(history.previous_employee)
        self.assertEqual(history.previous_employee_username, "lead_employee")
        self.assertEqual(
            history.reason, "Explicitly unassigned before deletion"
        )

    def test_history_insert_failure_rolls_back_assignment(self):
        with patch(
            "apps.leads.services.LeadAssignmentHistory.objects.create",
            side_effect=IntegrityError("simulated history failure"),
        ):
            with self.assertRaises(IntegrityError):
                set_lead_assignment(
                    self.assigned_lead,
                    self.other_employee,
                    self.admin,
                    "Should roll back",
                )
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)
        self.assertFalse(
            LeadAssignmentHistory.objects.filter(lead=self.assigned_lead).exists()
        )

    def test_stale_admin_form_cannot_overwrite_new_assignment(self):
        stale_lead = Lead.objects.get(pk=self.assigned_lead.pk)
        set_lead_assignment(
            self.assigned_lead, self.other_employee, self.admin, "Concurrent API change"
        )
        stale_lead.name = "Admin form edit"

        request = RequestFactory().post("/admin/apps/leads/lead/")
        request.user = self.admin
        form = SimpleNamespace(changed_data=["name"])
        LeadAdmin(Lead, admin.site).save_model(request, stale_lead, form, change=True)

        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.name, "Admin form edit")
        self.assertEqual(self.assigned_lead.assigned_to, self.other_employee)

    def test_employee_cannot_read_assignment_history(self):
        self.authenticate(self.employee)
        response = self.client.get(
            f"{LEADS_URL}{self.assigned_lead.pk}/assignment-history/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_cannot_use_bulk_assignment(self):
        self.authenticate(self.employee)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {"lead_ids": [self.assigned_lead.pk], "employee_id": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_bulk_assignment_changes_leads_and_records_each_change(self):
        unassigned = _make_lead("Bulk unassigned", "9876500020")
        already_assigned = _make_lead(
            "Bulk reassigned", "9876500021", assigned_to=self.employee
        )
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [unassigned.pk, already_assigned.pk],
                "employee_id": self.other_employee.pk,
                "reason": "Manual allocation",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"requested": 2, "changed": 2, "unchanged": 0})
        for lead in (unassigned, already_assigned):
            lead.refresh_from_db()
            self.assertEqual(lead.assigned_to, self.other_employee)
            history = LeadAssignmentHistory.objects.get(lead=lead)
            self.assertEqual(history.new_employee, self.other_employee)
            self.assertEqual(history.changed_by, self.admin)
            self.assertEqual(history.reason, "Manual allocation")
        self.assertIsNone(
            LeadAssignmentHistory.objects.get(lead=unassigned).previous_employee
        )
        self.assertEqual(
            LeadAssignmentHistory.objects.get(lead=already_assigned).previous_employee,
            self.employee,
        )

    def test_bulk_assignment_reports_unchanged_without_duplicate_history(self):
        unassigned = _make_lead("Bulk new", "9876500022")
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [self.assigned_lead.pk, unassigned.pk],
                "employee_id": self.employee.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"requested": 2, "changed": 1, "unchanged": 1})
        self.assertEqual(
            LeadAssignmentHistory.objects.filter(
                lead__in=[self.assigned_lead, unassigned]
            ).count(),
            1,
        )

    def test_bulk_assignment_explicitly_unassigns(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [self.assigned_lead.pk],
                "employee_id": None,
                "reason": "Manual unassignment",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assigned_lead.refresh_from_db()
        self.assertIsNone(self.assigned_lead.assigned_to)
        history = LeadAssignmentHistory.objects.get(lead=self.assigned_lead)
        self.assertEqual(history.previous_employee, self.employee)
        self.assertIsNone(history.new_employee)

    def test_bulk_assignment_rejects_duplicate_ids(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [self.assigned_lead.pk, self.assigned_lead.pk],
                "employee_id": self.other_employee.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)

    def test_bulk_assignment_rejects_empty_lead_list(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {"lead_ids": [], "employee_id": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_assignment_rejects_more_than_500_leads(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {"lead_ids": list(range(1, 502)), "employee_id": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_assignment_rejects_missing_ids_without_partial_changes(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [self.assigned_lead.pk, 99999999],
                "employee_id": self.other_employee.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)
        self.assertFalse(
            LeadAssignmentHistory.objects.filter(lead=self.assigned_lead).exists()
        )

    def test_bulk_assignment_rejects_archived_lead_without_partial_changes(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {
                "lead_ids": [self.assigned_lead.pk, self.archived_lead.pk],
                "employee_id": self.other_employee.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)
        self.assertFalse(LeadAssignmentHistory.objects.exists())

    def test_bulk_assignment_rejects_invalid_target_employee(self):
        self.authenticate(self.admin)
        response = self.client.post(
            BULK_ASSIGN_URL,
            {"lead_ids": [self.assigned_lead.pk], "employee_id": self.admin.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_assignment_is_atomic_if_processing_fails(self):
        first = _make_lead("Atomic first", "9876500023")
        second = _make_lead("Atomic second", "9876500024")
        self.authenticate(self.admin)
        calls = 0

        def fail_on_second(lead, employee, actor, reason=""):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise APIValidationError({"assigned_to": "Simulated assignment failure."})
            return real_set_lead_assignment(lead, employee, actor, reason)

        with patch(
            "apps.leads.api.views.set_lead_assignment",
            side_effect=fail_on_second,
        ):
            response = self.client.post(
                BULK_ASSIGN_URL,
                {
                    "lead_ids": [first.pk, second.pk],
                    "employee_id": self.other_employee.pk,
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.assigned_to)
        self.assertIsNone(second.assigned_to)
        self.assertFalse(LeadAssignmentHistory.objects.filter(lead__in=[first, second]).exists())

    def test_employee_lists_only_active_leads_assigned_to_self(self):
        self.authenticate(self.employee)
        response = self.client.get(LEADS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.json()["results"]
        self.assertEqual([row["id"] for row in rows], [self.assigned_lead.pk])
        self.assertNotIn("internal_notes", rows[0])

        # Administrative filters are rejected for employees with 400
        for admin_query in (
            f"assigned_to={self.other_employee.pk}",
            "unassigned=true",
            "archived=true",
        ):
            with self.subTest(query=admin_query):
                bad_resp = self.client.get(f"{LEADS_URL}?{admin_query}")
                self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_cannot_retrieve_another_employees_lead(self):
        self.authenticate(self.employee)
        response = self.client.get(f"{LEADS_URL}{self.other_lead.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_employee_cannot_retrieve_unassigned_lead(self):
        self.authenticate(self.employee)
        response = self.client.get(f"{LEADS_URL}{self.unassigned_lead.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_employee_cannot_retrieve_archived_lead(self):
        self.authenticate(self.employee)
        response = self.client.get(f"{LEADS_URL}{self.archived_lead.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_employee_cannot_create_or_reassign_leads(self):
        self.authenticate(self.employee)
        create_response = self.client.post(
            LEADS_URL, {"name": "Attempt", "phone": "9876500098"}, format="json"
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

        update_response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)
        put_response = self.client.put(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(put_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.assigned_to, self.employee)

    def test_admin_can_list_and_retrieve_archived_leads(self):
        self.authenticate(self.admin)
        active = self.client.get(LEADS_URL)
        self.assertEqual(active.status_code, status.HTTP_200_OK)
        active_ids = {lead["id"] for lead in active.json()["results"]}
        self.assertNotIn(self.archived_lead.pk, active_ids)

        archived = self.client.get(f"{LEADS_URL}?archived=true")
        self.assertEqual(archived.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [lead["id"] for lead in archived.json()["results"]],
            [self.archived_lead.pk],
        )
        detail = self.client.get(f"{LEADS_URL}{self.archived_lead.pk}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

    def test_admin_can_archive_without_deleting_record(self):
        self.authenticate(self.admin)
        response = self.client.patch(
            f"{LEADS_URL}{self.assigned_lead.pk}/",
            {"is_archived": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assigned_lead.refresh_from_db()
        self.assertTrue(self.assigned_lead.is_archived)
        self.assertTrue(Lead.objects.filter(pk=self.assigned_lead.pk).exists())

    def test_unauthenticated_access_is_rejected(self):
        list_response = self.client.get(LEADS_URL)
        create_response = self.client.post(
            LEADS_URL, {"name": "Anonymous", "phone": "9876500011"}, format="json"
        )
        detail_response = self.client.get(f"{LEADS_URL}{self.assigned_lead.pk}/")
        self.assertEqual(list_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(create_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(detail_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_pagination_works(self):
        for index in range(3):
            _make_lead(f"Pagination {index}", f"98765001{index:02d}")
        self.authenticate(self.admin)
        response = self.client.get(f"{LEADS_URL}?page_size=2")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["count"], 6)
        self.assertEqual(len(response.json()["results"]), 2)
        self.assertIsNotNone(response.json()["next"])

    def test_pagination_page_size_is_capped_at_100(self):
        Lead.objects.bulk_create(
            [
                Lead(name=f"Maximum page {index}", phone=f"99100{index:06d}")
                for index in range(101)
            ]
        )
        self.authenticate(self.admin)
        response = self.client.get(f"{LEADS_URL}?page_size=1000")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()["results"]), 100)
        self.assertIsNotNone(response.json()["next"])

    def test_employee_lead_status_and_lifecycle_update_endpoint(self):
        status_url = f"{LEADS_URL}{self.assigned_lead.pk}/status/"

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                status_url, {"status": Lead.Status.CONTACTED}, format="json"
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Admin and superuser with EMPLOYEE role -> 403
        superuser_emp = _make_user("su_lead_emp", User.Role.EMPLOYEE)
        superuser_emp.is_superuser = True
        superuser_emp.is_staff = True
        superuser_emp.save(update_fields=["is_superuser", "is_staff"])

        for non_ordinary_emp in (self.admin, superuser_emp):
            with self.subTest(actor=non_ordinary_emp.username):
                self.authenticate(non_ordinary_emp)
                forbidden_resp = self.client.post(
                    status_url,
                    {"status": Lead.Status.CONTACTED},
                    format="json",
                )
                self.assertEqual(
                    forbidden_resp.status_code, status.HTTP_403_FORBIDDEN
                )

        # Active assigned employee can update allowed lifecycle fields
        self.authenticate(self.employee)
        update_resp = self.client.post(
            status_url,
            {
                "status": Lead.Status.INTERESTED,
                "course_interest": "MBA International Business",
                "enquiry_details": "Requested brochure and fee structure.",
                "alternate_phone": "+91 98765 99999",
                "email": "student.updated@example.com",
            },
            format="json",
        )
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK, update_resp.json())
        data = update_resp.json()
        self.assertEqual(data["status"], Lead.Status.INTERESTED)
        self.assertEqual(data["course_interest"], "MBA International Business")
        self.assertEqual(
            data["enquiry_details"], "Requested brochure and fee structure."
        )
        self.assertEqual(data["alternate_phone"], "+91 98765 99999")
        self.assertEqual(data["email"], "student.updated@example.com")
        self.assertNotIn("internal_notes", data)
        self.assertNotIn("assigned_to", data)

        self.assigned_lead.refresh_from_db()
        self.assertEqual(self.assigned_lead.status, Lead.Status.INTERESTED)
        self.assertEqual(
            self.assigned_lead.course_interest, "MBA International Business"
        )

        # Unassigned, another employee's lead, archived lead, and nonexistent lead all return generic 404
        for target_pk in (
            self.unassigned_lead.pk,
            self.other_lead.pk,
            self.archived_lead.pk,
            9999999,
        ):
            with self.subTest(target_pk=target_pk):
                not_found_resp = self.client.post(
                    f"{LEADS_URL}{target_pk}/status/",
                    {"status": Lead.Status.CONTACTED},
                    format="json",
                )
                self.assertEqual(
                    not_found_resp.status_code, status.HTTP_404_NOT_FOUND
                )
                self.assertEqual(
                    not_found_resp.json().get("detail"), "Lead not found."
                )

        # Restricted or unknown fields, empty body, or invalid status return 400
        invalid_payloads = (
            {},
            {"status": "INVALID_STATUS"},
            {"name": "Renamed Student"},
            {"phone": "9999999999"},
            {"assigned_to": self.other_employee.pk},
            {"internal_notes": "Employee trying to edit internal notes"},
            {"is_archived": True},
            {"created_by": self.employee.pk},
            {"unknown_field": "value"},
            {"email": "not-an-email"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                bad_resp = self.client.post(status_url, payload, format="json")
                self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lead_search_filters_and_query_validation(self):
        self.employee.first_name = "Aisha"
        self.employee.last_name = "Khan"
        self.employee.save(update_fields=["first_name", "last_name"])

        self.assigned_lead.name = "Rahul Sharma"
        self.assigned_lead.email = "rahul.sharma@example.com"
        self.assigned_lead.alternate_phone = "9123456780"
        self.assigned_lead.course_interest = "B.Tech Computer Science"
        self.assigned_lead.status = Lead.Status.INTERESTED
        self.assigned_lead.save()

        self.other_lead.course_interest = "MBBS Abroad"
        self.other_lead.status = Lead.Status.CONTACTED
        self.other_lead.save()

        # Admin search across name, phone, alternate_phone, email, course_interest
        self.authenticate(self.admin)
        for query, expected_ids in (
            ("search=Rahul Sharma", [self.assigned_lead.pk]),
            ("search=9876500001", [self.assigned_lead.pk]),
            ("search=9123456780", [self.assigned_lead.pk]),
            ("search=rahul.sharma", [self.assigned_lead.pk]),
            ("search=computer science", [self.assigned_lead.pk]),
            (f"status={Lead.Status.INTERESTED}", [self.assigned_lead.pk]),
            (f"assigned_to={self.employee.pk}", [self.assigned_lead.pk]),
            ("unassigned=true", [self.unassigned_lead.pk]),
            (
                "unassigned=false",
                [self.other_lead.pk, self.assigned_lead.pk],
            ),
            ("archived=true", [self.archived_lead.pk]),
        ):
            with self.subTest(query=query):
                resp = self.client.get(f"{LEADS_URL}?{query}")
                self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.json())
                self.assertEqual(
                    [row["id"] for row in resp.json()["results"]],
                    expected_ids,
                )

        # Admin response includes assigned_to_username and assigned_to_name
        detail = self.client.get(f"{LEADS_URL}{self.assigned_lead.pk}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail.json()["assigned_to_username"], self.employee.username
        )
        self.assertEqual(detail.json()["assigned_to_name"], "Aisha Khan")

        unassigned_detail = self.client.get(f"{LEADS_URL}{self.unassigned_lead.pk}/")
        self.assertIsNone(unassigned_detail.json()["assigned_to_username"])
        self.assertIsNone(unassigned_detail.json()["assigned_to_name"])

        # Employee search is strictly scoped to own active leads
        self.authenticate(self.employee)
        emp_search_own = self.client.get(f"{LEADS_URL}?search=computer")
        self.assertEqual(emp_search_own.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in emp_search_own.json()["results"]],
            [self.assigned_lead.pk],
        )
        emp_search_other = self.client.get(f"{LEADS_URL}?search=MBBS")
        self.assertEqual(emp_search_other.status_code, status.HTTP_200_OK)
        self.assertEqual(emp_search_other.json()["results"], [])

        # Invalid and repeated query parameters return 400
        self.authenticate(self.admin)
        invalid_queries = (
            "unknown_filter=1",
            "status=NOT_VALID",
            "assigned_to=0",
            "assigned_to=abc",
            "unassigned=maybe",
            "archived=invalid",
            "page_size=0",
            "page_size=-5",
            "page_size=abc",
            "status=NEW&status=CONTACTED",
            "search=a&search=b",
            "page_size=10&page_size=20",
        )
        for query in invalid_queries:
            with self.subTest(invalid_query=query):
                bad_resp = self.client.get(f"{LEADS_URL}?{query}")
                self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Query count on GET /api/v1/leads/ is bounded (count + select_related page)
        self.client.credentials()
        self.client.force_authenticate(user=self.admin)
        with self.assertNumQueries(2):
            bounded_resp = self.client.get(LEADS_URL)
        self.assertEqual(bounded_resp.status_code, status.HTTP_200_OK)

    def test_single_lead_archived_assignment_and_unknown_field_rejection(self):
        self.authenticate(self.admin)

        # Cannot assign an already-archived lead via PATCH
        patch_archived = self.client.patch(
            f"{LEADS_URL}{self.archived_lead.pk}/",
            {"assigned_to": self.other_employee.pk},
            format="json",
        )
        self.assertEqual(patch_archived.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assigned_to", patch_archived.json())

        # Cannot simultaneously archive and assign a lead via PATCH
        archive_and_assign = self.client.patch(
            f"{LEADS_URL}{self.unassigned_lead.pk}/",
            {"is_archived": True, "assigned_to": self.employee.pk},
            format="json",
        )
        self.assertEqual(archive_and_assign.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assigned_to", archive_and_assign.json())

        # Cannot create an archived lead already assigned to an employee
        create_archived_assigned = self.client.post(
            LEADS_URL,
            {
                "name": "Archived On Create",
                "phone": "9876500991",
                "is_archived": True,
                "assigned_to": self.employee.pk,
            },
            format="json",
        )
        self.assertEqual(
            create_archived_assigned.status_code, status.HTTP_400_BAD_REQUEST
        )

        # Unassigning an archived lead (assigned_to=None) remains allowed
        unassign_archived = self.client.patch(
            f"{LEADS_URL}{self.archived_lead.pk}/",
            {"assigned_to": None, "assignment_reason": "Clear archived owner"},
            format="json",
        )
        self.assertEqual(unassign_archived.status_code, status.HTTP_200_OK)

        # Unknown fields rejected on lead create, update, and bulk-assign
        self.assertEqual(
            self.client.post(
                LEADS_URL,
                {
                    "name": "Unknown Field Lead",
                    "phone": "9876500992",
                    "unexpected": "nope",
                },
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.patch(
                f"{LEADS_URL}{self.assigned_lead.pk}/",
                {"unexpected": "nope"},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.post(
                BULK_ASSIGN_URL,
                {
                    "lead_ids": [self.assigned_lead.pk],
                    "employee_id": self.other_employee.pk,
                    "unexpected": "nope",
                },
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )


class LeadLifecycleConcurrencyTests(
    __import__("django.test", fromlist=["TransactionTestCase"]).TransactionTestCase
):
    """PostgreSQL concurrency test for lead reassignment racing with employee lifecycle update."""

    reset_sequences = True

    def setUp(self):
        self.admin = _make_user("conc_lead_admin", User.Role.ADMIN)
        self.employee = _make_user("conc_lead_emp1", User.Role.EMPLOYEE)
        self.other_employee = _make_user("conc_lead_emp2", User.Role.EMPLOYEE)
        self.lead = _make_lead(
            "Concurrent Lead",
            "9876509001",
            assigned_to=self.employee,
            status=Lead.Status.NEW,
        )

    def test_concurrent_reassignment_prevents_stale_employee_status_update(self):
        import threading
        from django.db import close_old_connections, connections
        from django.db.models.query import QuerySet
        from rest_framework.exceptions import NotFound
        from apps.leads.services import update_employee_lead_lifecycle

        admin_locked_lead = threading.Event()
        employee_waiting_for_lock = threading.Event()
        release_admin = threading.Event()
        outcomes = {}
        failures = {}
        original_get = QuerySet.get

        def synchronized_get(queryset, *args, **kwargs):
            thread_name = threading.current_thread().name
            is_lead_for_update = (
                queryset.model is Lead and queryset.query.select_for_update
            )
            if not is_lead_for_update:
                return original_get(queryset, *args, **kwargs)

            if thread_name == "admin-reassign":
                lead_obj = original_get(queryset, *args, **kwargs)
                admin_locked_lead.set()
                if not release_admin.wait(timeout=5):
                    raise TimeoutError("Timed out waiting to release admin lock")
                return lead_obj

            if thread_name == "employee-status-update":
                admin_locked_lead.wait(timeout=5)
                employee_waiting_for_lock.set()
                return original_get(queryset, *args, **kwargs)

            return original_get(queryset, *args, **kwargs)

        def admin_worker():
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                with transaction.atomic():
                    locked_lead = Lead.objects.select_for_update().get(pk=self.lead.pk)
                    set_lead_assignment(
                        locked_lead,
                        self.other_employee,
                        self.admin,
                        "Reassigned during employee update",
                    )
                outcomes["admin"] = "reassigned"
            except BaseException as exc:
                failures["admin"] = exc
            finally:
                connections.close_all()

        def employee_worker():
            close_old_connections()
            try:
                connections["default"].ensure_connection()
                update_employee_lead_lifecycle(
                    lead_id=self.lead.pk,
                    actor=self.employee,
                    status=Lead.Status.CONVERTED,
                )
                outcomes["employee"] = "updated"
            except NotFound:
                outcomes["employee"] = "not_found"
            except BaseException as exc:
                failures["employee"] = exc
            finally:
                connections.close_all()

        t_admin = threading.Thread(
            target=admin_worker, name="admin-reassign", daemon=True
        )
        t_emp = threading.Thread(
            target=employee_worker, name="employee-status-update", daemon=True
        )
        try:
            with patch.object(QuerySet, "get", new=synchronized_get):
                t_admin.start()
                self.assertTrue(admin_locked_lead.wait(timeout=5))
                t_emp.start()
                self.assertTrue(employee_waiting_for_lock.wait(timeout=5))
                release_admin.set()
                t_admin.join(timeout=5)
                t_emp.join(timeout=5)
        finally:
            release_admin.set()
            for t in (t_admin, t_emp):
                if t.ident is not None:
                    t.join(timeout=5)

        self.assertFalse(failures, f"Unexpected worker errors: {failures!r}")
        self.assertEqual(outcomes.get("admin"), "reassigned")
        self.assertEqual(outcomes.get("employee"), "not_found")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_to, self.other_employee)
        self.assertEqual(self.lead.status, Lead.Status.NEW)
