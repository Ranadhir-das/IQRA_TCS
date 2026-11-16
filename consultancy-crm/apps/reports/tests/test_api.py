"""Comprehensive tests for Phase 4.1 Admin Dashboard, Employee Performance, and Exports."""

import csv
from datetime import datetime, timedelta
import io
import uuid
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from django.db import models
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.reports.exporters import (
    CSV_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    render_csv_bytes,
    render_xlsx_bytes,
    sanitize_cell_value,
    sanitize_filename,
)

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
UTC_TZ = ZoneInfo("UTC")

DASHBOARD_URL = "/api/v1/reports/dashboard/"
EMPLOYEES_URL = "/api/v1/reports/employees/"
EXPORT_URL = "/api/v1/reports/export/"

XML_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def parse_xlsx_rows(xlsx_bytes: bytes) -> tuple[list[list[str]], list[list[tuple[str, str]]]]:
    """Parse xl/worksheets/sheet1.xml from XLSX bytes into values and (cell_type, value) pairs."""
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        names = set(zf.namelist())
        required_members = {
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/styles.xml",
            "xl/worksheets/sheet1.xml",
        }
        assert required_members.issubset(names), f"Missing archive entries: {required_members - names}"
        for member in required_members:
            ET.fromstring(zf.read(member))
        sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    text_rows: list[list[str]] = []
    typed_rows: list[list[tuple[str, str]]] = []
    for row_el in sheet_root.findall(".//main:sheetData/main:row", XML_NS):
        row_vals: list[str] = []
        row_typed: list[tuple[str, str]] = []
        for cell_el in row_el.findall("main:c", XML_NS):
            cell_type = cell_el.attrib.get("t", "")
            if cell_type == "n":
                v_el = cell_el.find("main:v", XML_NS)
                val = v_el.text if v_el is not None and v_el.text is not None else ""
            else:
                t_el = cell_el.find("main:is/main:t", XML_NS)
                val = t_el.text if t_el is not None and t_el.text is not None else ""
            row_vals.append(val)
            row_typed.append((cell_type, val))
        text_rows.append(row_vals)
        typed_rows.append(row_typed)
    return text_rows, typed_rows


class ReportingBaseTestCase(TestCase):
    """Shared fixtures and helper factories for Phase 4.1 reporting tests."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="report_admin",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Report",
            last_name="Admin",
            email="admin@iqracentral.com",
            is_active=True,
        )
        cls.inactive_admin = User.objects.create_user(
            username="inactive_admin",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            is_active=False,
        )
        cls.superuser_employee = User.objects.create_user(
            username="super_emp",
            password="SuperPassword123!",
            role=User.Role.EMPLOYEE,
            first_name="Super",
            last_name="Employee",
            is_superuser=True,
            is_staff=True,
            is_active=True,
        )
        cls.emp_alice = User.objects.create_user(
            username="alice_emp",
            password="EmpPassword123!",
            role=User.Role.EMPLOYEE,
            first_name="Alice",
            last_name="Khan",
            email="alice.private@iqracentral.com",
            designation="Senior Telecaller",
            is_active=True,
        )
        cls.emp_bob = User.objects.create_user(
            username="bob_emp",
            password="EmpPassword123!",
            role=User.Role.EMPLOYEE,
            first_name="Bob",
            last_name="Sharma",
            email="bob.private@iqracentral.com",
            designation="Junior Telecaller",
            is_active=True,
        )
        cls.emp_inactive = User.objects.create_user(
            username="charlie_inactive",
            password="EmpPassword123!",
            role=User.Role.EMPLOYEE,
            first_name="Charlie",
            last_name="Former",
            email="charlie.private@iqracentral.com",
            designation="Telecaller",
            is_active=False,
        )

        cls.outcome_interested, _ = CallOutcome.objects.get_or_create(
            code="INTERESTED",
            defaults={
                "label": "Interested",
                "is_active": True,
                "display_order": 1,
            },
        )
        cls.outcome_no_answer, _ = CallOutcome.objects.get_or_create(
            code="NO_ANSWER",
            defaults={
                "label": "No Answer",
                "is_active": True,
                "display_order": 2,
            },
        )

    def setUp(self):
        self.client = APIClient()

    def _create_lead(
        self,
        *,
        name: str,
        phone: str,
        status_val: str = Lead.Status.NEW,
        assigned_to: User | None = None,
        is_archived: bool = False,
        created_at: datetime | None = None,
        internal_notes: str = "Secret internal admin note",
        course_interest: str = "MBBS",
        email: str = "",
    ) -> Lead:
        lead = Lead.objects.create(
            name=name,
            phone=phone,
            email=email,
            course_interest=course_interest,
            status=status_val,
            assigned_to=assigned_to,
            is_archived=is_archived,
            internal_notes=internal_notes,
            created_by=self.admin,
        )
        if created_at is not None:
            Lead.objects.filter(pk=lead.pk).update(created_at=created_at)
            lead.refresh_from_db()
        return lead

    def _create_call(
        self,
        *,
        lead: Lead,
        caller: User,
        outcome: CallOutcome,
        started_at: datetime,
        duration_seconds: int = 120,
        employee_notes: str = "Discussed syllabus",
    ) -> Call:
        ended_at = started_at + timedelta(seconds=duration_seconds)
        return Call.objects.create(
            client_event_id=uuid.uuid4(),
            request_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
            lead=lead,
            caller=caller,
            caller_username_snapshot=caller.username,
            caller_name_snapshot=caller.get_full_name(),
            outcome=outcome,
            outcome_code_snapshot=outcome.code,
            outcome_label_snapshot=outcome.label,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration_seconds,
            employee_notes=employee_notes,
        )

    def _create_followup(
        self,
        *,
        lead: Lead,
        owner: User,
        scheduled_at: datetime,
        status_val: str = FollowUp.Status.PENDING,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        notes: str = "Follow up with brochure",
        call: Call | None = None,
    ) -> FollowUp:
        fu = FollowUp.objects._create_from_service(
            lead=lead,
            call=call,
            owner=owner,
            owner_username_snapshot=owner.username,
            owner_name_snapshot=owner.get_full_name(),
            scheduled_at=scheduled_at,
            status=status_val,
            notes=notes,
            completed_at=completed_at,
            cancelled_at=cancelled_at,
        )
        if created_at is not None:
            models.QuerySet.update(
                FollowUp.objects.filter(pk=fu.pk),
                created_at=created_at,
            )
            fu.refresh_from_db()
        return fu


class ReportingPermissionsTest(ReportingBaseTestCase):
    """Verify strict access control across all reporting endpoints."""

    def test_anonymous_requests_return_401(self):
        for url in (
            DASHBOARD_URL,
            EMPLOYEES_URL,
            f"{EXPORT_URL}?report_type=employee_performance",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_active_employee_requests_return_403(self):
        self.client.force_authenticate(user=self.emp_alice)
        for url in (
            DASHBOARD_URL,
            EMPLOYEES_URL,
            f"{EXPORT_URL}?report_type=employee_performance",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_employee_requests_return_403(self):
        self.client.force_authenticate(user=self.emp_inactive)
        for url in (
            DASHBOARD_URL,
            EMPLOYEES_URL,
            f"{EXPORT_URL}?report_type=employee_performance",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_admin_requests_return_403(self):
        self.client.force_authenticate(user=self.inactive_admin)
        for url in (
            DASHBOARD_URL,
            EMPLOYEES_URL,
            f"{EXPORT_URL}?report_type=employee_performance",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_active_admin_and_active_superuser_employee_receive_200(self):
        for actor in (self.admin, self.superuser_employee):
            self.client.force_authenticate(user=actor)
            for url in (
                DASHBOARD_URL,
                EMPLOYEES_URL,
                f"{EXPORT_URL}?report_type=employee_performance",
            ):
                with self.subTest(actor=actor.username, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_with_employee_role_excluded_from_employee_rows_and_exports(self):
        self.client.force_authenticate(user=self.superuser_employee)
        emp_res = self.client.get(EMPLOYEES_URL)
        self.assertEqual(emp_res.status_code, status.HTTP_200_OK)
        returned_usernames = [row["username"] for row in emp_res.data["results"]]
        self.assertNotIn(self.superuser_employee.username, returned_usernames)
        self.assertCountEqual(
            returned_usernames,
            ["alice_emp", "bob_emp", "charlie_inactive"],
        )

        # Filtering by superuser_employee ID must be rejected with 400
        bad_filter = self.client.get(
            DASHBOARD_URL,
            {"employee_id": self.superuser_employee.id},
        )
        self.assertEqual(bad_filter.status_code, status.HTTP_400_BAD_REQUEST)


class DashboardAggregationAndTimezoneTest(ReportingBaseTestCase):
    """Verify exact dashboard metrics, Asia/Kolkata boundaries, and snapshot preservation."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

        self.now_local = timezone.now().astimezone(KOLKATA_TZ)
        self.today_local = self.now_local.date()
        self.today_midnight = datetime(
            self.today_local.year,
            self.today_local.month,
            self.today_local.day,
            0,
            0,
            0,
            tzinfo=KOLKATA_TZ,
        )
        self.tomorrow_midnight = self.today_midnight + timedelta(days=1)

        # Fixed test window: 2026-11-10 to 2026-11-14 (Asia/Kolkata)
        self.period_start = datetime(2026, 11, 10, 0, 0, 0, tzinfo=KOLKATA_TZ)
        self.period_end_exclusive = datetime(2026, 11, 15, 0, 0, 0, tzinfo=KOLKATA_TZ)

        # Leads:
        # 1. Active NEW assigned to Alice, created inside period
        self.lead_alice_new = self._create_lead(
            name="Lead Alice New",
            phone="+919811111111",
            status_val=Lead.Status.NEW,
            assigned_to=self.emp_alice,
            created_at=datetime(2026, 11, 10, 0, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 2. Active CONVERTED assigned to Alice, created inside period
        self.lead_alice_conv = self._create_lead(
            name="Lead Alice Converted",
            phone="+919822222222",
            status_val=Lead.Status.CONVERTED,
            assigned_to=self.emp_alice,
            created_at=datetime(2026, 11, 14, 23, 59, 59, 999999, tzinfo=KOLKATA_TZ),
        )
        # 3. Active CONTACTED assigned to Bob, created BEFORE period (1 microsecond before start)
        self.lead_bob_contacted = self._create_lead(
            name="Lead Bob Contacted",
            phone="+919833333333",
            status_val=Lead.Status.CONTACTED,
            assigned_to=self.emp_bob,
            created_at=datetime(2026, 11, 9, 23, 59, 59, 999999, tzinfo=KOLKATA_TZ),
        )
        # 4. Active INTERESTED assigned to inactive Charlie, created AFTER period (exact exclusive end)
        self.lead_inactive_owner = self._create_lead(
            name="Lead Inactive Charlie",
            phone="+919844444444",
            status_val=Lead.Status.INTERESTED,
            assigned_to=self.emp_inactive,
            created_at=datetime(2026, 11, 15, 0, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 5. Active NEW unassigned, created inside period
        self.lead_unassigned = self._create_lead(
            name="Lead Unassigned",
            phone="+919855555555",
            status_val=Lead.Status.NEW,
            assigned_to=None,
            created_at=datetime(2026, 11, 12, 12, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 6. Archived NOT_INTERESTED assigned to Alice, created inside period
        self.lead_archived = self._create_lead(
            name="Lead Archived Not Interested",
            phone="+919866666666",
            status_val=Lead.Status.NOT_INTERESTED,
            assigned_to=self.emp_alice,
            is_archived=True,
            created_at=datetime(2026, 11, 11, 15, 0, 0, tzinfo=KOLKATA_TZ),
        )

        # Calls:
        # Call 1 (Alice): at exact start boundary 2026-11-10 00:00:00+05:30 (2026-11-09 18:30:00Z)
        self.call_1 = self._create_call(
            lead=self.lead_alice_new,
            caller=self.emp_alice,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 10, 0, 0, 0, tzinfo=KOLKATA_TZ),
            duration_seconds=100,
        )
        # Call 2 (Alice): same lead on 2026-11-14 01:00:00+05:30 (which is 2026-11-13 19:30:00Z in UTC!)
        self.call_2 = self._create_call(
            lead=self.lead_alice_new,
            caller=self.emp_alice,
            outcome=self.outcome_no_answer,
            started_at=datetime(2026, 11, 13, 19, 30, 0, tzinfo=UTC_TZ),
            duration_seconds=50,
        )
        # Call 3 (Bob): on 2026-11-14 23:59:59+05:30 (included)
        self.call_3 = self._create_call(
            lead=self.lead_bob_contacted,
            caller=self.emp_bob,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 14, 23, 59, 59, tzinfo=KOLKATA_TZ),
            duration_seconds=150,
        )
        # Call 4 (Alice): 1 microsecond before period start (excluded)
        self._create_call(
            lead=self.lead_alice_conv,
            caller=self.emp_alice,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 9, 23, 59, 59, 999999, tzinfo=KOLKATA_TZ),
            duration_seconds=500,
        )
        # Call 5 (Bob): at exact exclusive end 2026-11-15 00:00:00+05:30 (excluded)
        self._create_call(
            lead=self.lead_bob_contacted,
            caller=self.emp_bob,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 15, 0, 0, 0, tzinfo=KOLKATA_TZ),
            duration_seconds=600,
        )

        # Rename CallOutcome label after calls are logged to prove immutable snapshot usage
        CallOutcome.objects.filter(pk=self.outcome_interested.pk).update(
            label="Renamed Outcome Label"
        )

        # Follow-ups:
        # 1. Pending overdue (Alice): scheduled 1 second before today's Kolkata midnight, created in period
        self.fu_overdue_alice = self._create_followup(
            lead=self.lead_alice_new,
            owner=self.emp_alice,
            scheduled_at=self.today_midnight - timedelta(seconds=1),
            status_val=FollowUp.Status.PENDING,
            created_at=datetime(2026, 11, 11, 10, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 2. Pending due today (Alice): scheduled at today's Kolkata noon, created outside period
        self.fu_today_alice = self._create_followup(
            lead=self.lead_alice_conv,
            owner=self.emp_alice,
            scheduled_at=self.today_midnight + timedelta(hours=12),
            status_val=FollowUp.Status.PENDING,
            created_at=datetime(2026, 11, 1, 10, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 3. Pending upcoming (Bob): scheduled at tomorrow's Kolkata midnight, created in period
        self.fu_upcoming_bob = self._create_followup(
            lead=self.lead_bob_contacted,
            owner=self.emp_bob,
            scheduled_at=self.tomorrow_midnight,
            status_val=FollowUp.Status.PENDING,
            created_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 4. Pending overdue (Inactive Charlie): scheduled 2 days ago, created outside period
        self.fu_overdue_inactive = self._create_followup(
            lead=self.lead_inactive_owner,
            owner=self.emp_inactive,
            scheduled_at=self.today_midnight - timedelta(days=2),
            status_val=FollowUp.Status.PENDING,
            created_at=datetime(2026, 11, 2, 10, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 5. Completed in period (Alice)
        self.fu_completed_alice = self._create_followup(
            lead=self.lead_alice_new,
            owner=self.emp_alice,
            scheduled_at=datetime(2026, 11, 12, 9, 0, 0, tzinfo=KOLKATA_TZ),
            status_val=FollowUp.Status.COMPLETED,
            created_at=datetime(2026, 11, 10, 9, 0, 0, tzinfo=KOLKATA_TZ),
            completed_at=datetime(2026, 11, 13, 14, 0, 0, tzinfo=KOLKATA_TZ),
        )
        # 6. Cancelled in period (Bob)
        self.fu_cancelled_bob = self._create_followup(
            lead=self.lead_bob_contacted,
            owner=self.emp_bob,
            scheduled_at=datetime(2026, 11, 13, 9, 0, 0, tzinfo=KOLKATA_TZ),
            status_val=FollowUp.Status.CANCELLED,
            created_at=datetime(2026, 11, 5, 9, 0, 0, tzinfo=KOLKATA_TZ),
            cancelled_at=datetime(2026, 11, 14, 16, 0, 0, tzinfo=KOLKATA_TZ),
        )

        # LeadAssignmentHistory:
        # 1. Inside period involving Alice
        h1 = LeadAssignmentHistory.objects.create(
            lead=self.lead_alice_new,
            previous_employee=None,
            new_employee=self.emp_alice,
            changed_by=self.admin,
            reason="Initial assignment",
        )
        LeadAssignmentHistory.objects.filter(pk=h1.pk).update(
            changed_at=datetime(2026, 11, 11, 12, 0, 0, tzinfo=KOLKATA_TZ)
        )
        # 2. Inside period involving Bob only
        h2 = LeadAssignmentHistory.objects.create(
            lead=self.lead_bob_contacted,
            previous_employee=None,
            new_employee=self.emp_bob,
            changed_by=self.admin,
            reason="Bob assignment",
        )
        LeadAssignmentHistory.objects.filter(pk=h2.pk).update(
            changed_at=datetime(2026, 11, 12, 12, 0, 0, tzinfo=KOLKATA_TZ)
        )
        # 3. Outside period
        h3 = LeadAssignmentHistory.objects.create(
            lead=self.lead_inactive_owner,
            previous_employee=self.emp_alice,
            new_employee=self.emp_inactive,
            changed_by=self.admin,
            reason="Old assignment",
        )
        LeadAssignmentHistory.objects.filter(pk=h3.pk).update(
            changed_at=datetime(2026, 11, 1, 12, 0, 0, tzinfo=KOLKATA_TZ)
        )

    def test_dashboard_organization_wide_summary(self):
        response = self.client.get(
            DASHBOARD_URL,
            {"date_from": "2026-11-10", "date_to": "2026-11-14"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(
            data["period"],
            {
                "timezone": "Asia/Kolkata",
                "date_from": "2026-11-10",
                "date_to": "2026-11-14",
                "start_at": "2026-11-10T00:00:00+05:30",
                "end_exclusive_at": "2026-11-15T00:00:00+05:30",
                "employee_id": None,
            },
        )

        # A. Leads
        self.assertEqual(
            data["leads"],
            {
                "total_active": 5,
                "total_archived": 1,
                "assigned_active": 4,
                "unassigned_active": 1,
                "created_in_period": 4,  # alice_new, alice_conv, unassigned, archived_not_interested
                "current_active_by_status": {
                    "NEW": 2,
                    "CONTACTED": 1,
                    "INTERESTED": 1,
                    "NOT_INTERESTED": 0,
                    "CONVERTED": 1,
                },
                "created_in_period_by_current_status": {
                    "NEW": 2,
                    "CONTACTED": 0,
                    "INTERESTED": 0,
                    "NOT_INTERESTED": 1,
                    "CONVERTED": 1,
                },
                "filtered_employee_leads": None,
            },
        )

        # B. Calls (with immutable outcome label snapshot "Interested", not "Renamed Outcome Label")
        self.assertEqual(
            data["calls"],
            {
                "total_calls": 3,
                "unique_leads_called": 2,
                "total_duration_seconds": 300,
                "average_duration_seconds": 100.0,
                "by_outcome": [
                    {
                        "outcome_code": "INTERESTED",
                        "outcome_label": "Interested",
                        "count": 2,
                    },
                    {
                        "outcome_code": "NO_ANSWER",
                        "outcome_label": "No Answer",
                        "count": 1,
                    },
                ],
                # Call 2 was at 2026-11-13 19:30 UTC -> 2026-11-14 01:00+05:30 (grouped into 2026-11-14!)
                "daily_trends": [
                    {
                        "date": "2026-11-10",
                        "total_calls": 1,
                        "unique_leads_called": 1,
                        "total_duration_seconds": 100,
                    },
                    {
                        "date": "2026-11-14",
                        "total_calls": 2,
                        "unique_leads_called": 2,
                        "total_duration_seconds": 200,
                    },
                ],
            },
        )

        # C. Follow-ups
        self.assertEqual(
            data["follow_ups"],
            {
                "pending_total": 4,
                "overdue": 2,
                "due_today": 1,
                "upcoming": 1,
                "created_in_period": 3,
                "completed_in_period": 1,
                "cancelled_in_period": 1,
            },
        )

        # D. Workload (super_emp is excluded from non-superuser EMPLOYEE counts)
        self.assertEqual(
            data["workload"],
            {
                "total_employees": 3,
                "active_employees": 2,
                "inactive_employees": 1,
                "inactive_employees_with_active_leads_count": 1,
                "inactive_employees_with_pending_followups_count": 1,
                "active_leads_with_inactive_owner_count": 1,
                "pending_followups_with_inactive_owner_count": 1,
                "assignments_changed_in_period": 2,
                "filtered_employee_workload": None,
            },
        )

    def test_dashboard_filtered_by_employee_preserves_org_totals_and_populates_employee_sections(self):
        response = self.client.get(
            DASHBOARD_URL,
            {
                "date_from": "2026-11-10",
                "date_to": "2026-11-14",
                "employee_id": self.emp_alice.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(data["period"]["employee_id"], self.emp_alice.id)
        # Organization-wide lead totals remain unchanged
        self.assertEqual(data["leads"]["total_active"], 5)
        self.assertEqual(data["leads"]["unassigned_active"], 1)
        self.assertEqual(
            data["leads"]["filtered_employee_leads"],
            {
                "employee_id": self.emp_alice.id,
                "assigned_active": 2,
                "assigned_archived": 1,
                "created_in_period_assigned_to_employee": 3,
                "current_active_by_status": {
                    "NEW": 1,
                    "CONTACTED": 0,
                    "INTERESTED": 0,
                    "NOT_INTERESTED": 0,
                    "CONVERTED": 1,
                },
            },
        )

        # Calls scoped to Alice
        self.assertEqual(data["calls"]["total_calls"], 2)
        self.assertEqual(data["calls"]["unique_leads_called"], 1)
        self.assertEqual(data["calls"]["total_duration_seconds"], 150)
        self.assertEqual(data["calls"]["average_duration_seconds"], 75.0)

        # Follow-ups scoped to Alice
        self.assertEqual(
            data["follow_ups"],
            {
                "pending_total": 2,
                "overdue": 1,
                "due_today": 1,
                "upcoming": 0,
                "created_in_period": 2,
                "completed_in_period": 1,
                "cancelled_in_period": 0,
            },
        )

        # Workload org-wide counts remain unchanged while filtered_employee_workload is populated
        self.assertEqual(data["workload"]["total_employees"], 3)
        self.assertEqual(data["workload"]["assignments_changed_in_period"], 2)
        self.assertEqual(
            data["workload"]["filtered_employee_workload"],
            {
                "employee_id": self.emp_alice.id,
                "assigned_active_leads_count": 2,
                "pending_followups_count": 2,
                "overdue_followups_count": 1,
                "assignments_changed_in_period": 1,
            },
        )

    def test_default_30_day_window_and_single_date_boundary_semantics(self):
        # Default 30-day window
        res_default = self.client.get(DASHBOARD_URL)
        self.assertEqual(res_default.status_code, status.HTTP_200_OK)
        expected_from = (self.today_local - timedelta(days=29)).isoformat()
        expected_to = self.today_local.isoformat()
        self.assertEqual(res_default.data["period"]["date_from"], expected_from)
        self.assertEqual(res_default.data["period"]["date_to"], expected_to)

        # Only date_from supplied -> single day [date_from, date_from]
        res_from_only = self.client.get(DASHBOARD_URL, {"date_from": "2026-11-10"})
        self.assertEqual(res_from_only.status_code, status.HTTP_200_OK)
        self.assertEqual(res_from_only.data["period"]["date_from"], "2026-11-10")
        self.assertEqual(res_from_only.data["period"]["date_to"], "2026-11-10")
        self.assertEqual(res_from_only.data["calls"]["total_calls"], 1)

        # Only date_to supplied -> single day [date_to, date_to]
        res_to_only = self.client.get(DASHBOARD_URL, {"date_to": "2026-11-14"})
        self.assertEqual(res_to_only.status_code, status.HTTP_200_OK)
        self.assertEqual(res_to_only.data["period"]["date_from"], "2026-11-14")
        self.assertEqual(res_to_only.data["period"]["date_to"], "2026-11-14")
        self.assertEqual(res_to_only.data["calls"]["total_calls"], 2)


class EmployeePerformanceReportTest(ReportingBaseTestCase):
    """Verify per-employee workload and period performance endpoint."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

        self.today_local = timezone.now().astimezone(KOLKATA_TZ).date()
        self.today_midnight = datetime(
            self.today_local.year,
            self.today_local.month,
            self.today_local.day,
            0,
            0,
            0,
            tzinfo=KOLKATA_TZ,
        )

        # Alice: 2 active leads (1 converted), 1 archived lead
        self.lead_a1 = self._create_lead(
            name="Alice Lead 1",
            phone="+919700000001",
            status_val=Lead.Status.CONVERTED,
            assigned_to=self.emp_alice,
        )
        self.lead_a2 = self._create_lead(
            name="Alice Lead 2",
            phone="+919700000002",
            status_val=Lead.Status.NEW,
            assigned_to=self.emp_alice,
        )
        self._create_lead(
            name="Alice Lead Archived",
            phone="+919700000003",
            status_val=Lead.Status.CONVERTED,
            assigned_to=self.emp_alice,
            is_archived=True,
        )

        # Alice calls in period: 2 calls on lead_a1 (60s, 90s)
        self._create_call(
            lead=self.lead_a1,
            caller=self.emp_alice,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 10, 11, 0, 0, tzinfo=KOLKATA_TZ),
            duration_seconds=60,
        )
        self._create_call(
            lead=self.lead_a1,
            caller=self.emp_alice,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 11, 11, 0, 0, tzinfo=KOLKATA_TZ),
            duration_seconds=90,
        )

        # Alice follow-ups: 1 overdue pending, 1 completed in period, 1 cancelled in period
        self._create_followup(
            lead=self.lead_a1,
            owner=self.emp_alice,
            scheduled_at=self.today_midnight - timedelta(hours=2),
            status_val=FollowUp.Status.PENDING,
        )
        self._create_followup(
            lead=self.lead_a2,
            owner=self.emp_alice,
            scheduled_at=datetime(2026, 11, 10, 15, 0, 0, tzinfo=KOLKATA_TZ),
            status_val=FollowUp.Status.COMPLETED,
            completed_at=datetime(2026, 11, 10, 16, 0, 0, tzinfo=KOLKATA_TZ),
        )
        self._create_followup(
            lead=self.lead_a2,
            owner=self.emp_alice,
            scheduled_at=datetime(2026, 11, 11, 15, 0, 0, tzinfo=KOLKATA_TZ),
            status_val=FollowUp.Status.CANCELLED,
            cancelled_at=datetime(2026, 11, 11, 16, 0, 0, tzinfo=KOLKATA_TZ),
        )

    def test_employee_performance_list_ordering_metrics_and_scopes(self):
        response = self.client.get(
            EMPLOYEES_URL,
            {"date_from": "2026-11-10", "date_to": "2026-11-14"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(data["count"], 3)
        self.assertIn("period", data)
        self.assertIn("metric_scopes", data["period"])
        self.assertEqual(
            data["period"]["metric_scopes"]["current_workload_fields"],
            [
                "assigned_active_leads_count",
                "converted_active_leads_count",
                "pending_followups_count",
                "overdue_followups_count",
            ],
        )

        results = data["results"]
        # Ordered by (-is_active, username, id): alice_emp (True), bob_emp (True), charlie_inactive (False)
        self.assertEqual(
            [r["username"] for r in results],
            ["alice_emp", "bob_emp", "charlie_inactive"],
        )

        alice_row = results[0]
        self.assertEqual(
            alice_row,
            {
                "id": self.emp_alice.id,
                "username": "alice_emp",
                "full_name": "Alice Khan",
                "designation": "Senior Telecaller",
                "is_active": True,
                "assigned_active_leads_count": 2,
                "converted_active_leads_count": 1,
                "pending_followups_count": 1,
                "overdue_followups_count": 1,
                "calls_count": 2,
                "unique_leads_called_count": 1,
                "total_call_duration_seconds": 150,
                "average_call_duration_seconds": 75.0,
                "followups_completed_count": 1,
                "followups_cancelled_count": 1,
            },
        )
        self.assertNotIn("email", alice_row)
        self.assertNotIn("password", alice_row)

        bob_row = results[1]
        self.assertEqual(bob_row["calls_count"], 0)
        self.assertEqual(bob_row["average_call_duration_seconds"], 0.0)

    def test_employee_performance_filters_and_pagination(self):
        # Filter by is_active=false
        res_inactive = self.client.get(EMPLOYEES_URL, {"is_active": "false"})
        self.assertEqual(res_inactive.status_code, status.HTTP_200_OK)
        self.assertEqual(res_inactive.data["count"], 1)
        self.assertEqual(
            res_inactive.data["results"][0]["username"],
            "charlie_inactive",
        )

        # Filter by employee_id
        res_emp = self.client.get(
            EMPLOYEES_URL,
            {"employee_id": self.emp_alice.id},
        )
        self.assertEqual(res_emp.status_code, status.HTTP_200_OK)
        self.assertEqual(res_emp.data["count"], 1)
        self.assertEqual(res_emp.data["results"][0]["id"], self.emp_alice.id)

        # Pagination page_size=2
        res_page = self.client.get(EMPLOYEES_URL, {"page": "1", "page_size": "2"})
        self.assertEqual(res_page.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_page.data["results"]), 2)
        self.assertIsNotNone(res_page.data["next"])


class ReportingValidationErrorsTest(ReportingBaseTestCase):
    """Verify strict 400 validation on invalid parameters across all reporting views."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

    def test_unknown_and_repeated_query_parameters_return_400(self):
        for url in (DASHBOARD_URL, EMPLOYEES_URL, EXPORT_URL):
            with self.subTest(url=url, case="unknown"):
                res = self.client.get(url, {"unknown_param": "1"})
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        res_repeat = self.client.get(
            f"{DASHBOARD_URL}?date_from=2026-11-10&date_from=2026-11-11"
        )
        self.assertEqual(res_repeat.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_dates_and_reversed_or_excessive_ranges_return_400(self):
        invalid_dates = [
            "2026-11-10T00:00:00Z",
            "2026-02-30",
            "10-11-2026",
            "2026/11/10",
            "not-a-date",
        ]
        for bad_date in invalid_dates:
            with self.subTest(bad_date=bad_date):
                res = self.client.get(DASHBOARD_URL, {"date_from": bad_date})
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Reversed range
        res_rev = self.client.get(
            DASHBOARD_URL,
            {"date_from": "2026-11-15", "date_to": "2026-11-10"},
        )
        self.assertEqual(res_rev.status_code, status.HTTP_400_BAD_REQUEST)

        # Exceeding 366 inclusive calendar days (2026-01-01 to 2027-01-02 is 367 days)
        res_too_wide = self.client.get(
            DASHBOARD_URL,
            {"date_from": "2026-01-01", "date_to": "2027-01-02"},
        )
        self.assertEqual(res_too_wide.status_code, status.HTTP_400_BAD_REQUEST)

        # Exactly 366 inclusive days (2026-01-01 to 2027-01-01) is allowed
        res_max_ok = self.client.get(
            DASHBOARD_URL,
            {"date_from": "2026-01-01", "date_to": "2027-01-01"},
        )
        self.assertEqual(res_max_ok.status_code, status.HTTP_200_OK)

    def test_invalid_employee_id_and_booleans_and_pagination_return_400(self):
        for bad_emp_id in ("0", "-5", "abc", "999999", str(self.admin.id)):
            with self.subTest(bad_emp_id=bad_emp_id):
                res = self.client.get(DASHBOARD_URL, {"employee_id": bad_emp_id})
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        for bad_bool in ("1", "0", "yes", "maybe"):
            with self.subTest(bad_bool=bad_bool):
                res = self.client.get(EMPLOYEES_URL, {"is_active": bad_bool})
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        for param in ("page", "page_size"):
            for bad_val in ("0", "-1", "abc", "1.5"):
                with self.subTest(param=param, bad_val=bad_val):
                    res = self.client.get(EMPLOYEES_URL, {param: bad_val})
                    self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_export_specific_filter_validation_returns_400(self):
        # Missing report_type
        self.assertEqual(
            self.client.get(EXPORT_URL).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Unsupported report_type
        self.assertEqual(
            self.client.get(EXPORT_URL, {"report_type": "invalid"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Unsupported export_format
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "calls", "export_format": "pdf"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Disallowed cross-report filter: outcome_code on leads
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "leads", "outcome_code": "INTERESTED"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Disallowed cross-report filter: due_state on calls
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "calls", "due_state": "today"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Invalid lead status
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "leads", "status": "PENDING"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Invalid followup status
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "followups", "status": "CONVERTED"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        # Invalid archived value
        self.assertEqual(
            self.client.get(
                EXPORT_URL,
                {"report_type": "leads", "archived": "invalid"},
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )


class ReportExportAndSecurityTest(ReportingBaseTestCase):
    """Verify CSV/XLSX exports, formula-injection defenses, XML safety, and row limits."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

        self.lead_formula = self._create_lead(
            name="=CMD|' /C calc'!A0",
            phone="+919900001111",
            email="  @SUM(1,2)",
            course_interest="\t=HYPERLINK(\"http://evil.example\")",
            status_val=Lead.Status.NEW,
            assigned_to=self.emp_alice,
            created_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=KOLKATA_TZ),
            internal_notes="CONFIDENTIAL_INTERNAL_NOTE_DO_NOT_EXPORT",
        )
        self.call_formula = self._create_call(
            lead=self.lead_formula,
            caller=self.emp_alice,
            outcome=self.outcome_interested,
            started_at=datetime(2026, 11, 12, 11, 0, 0, tzinfo=KOLKATA_TZ),
            duration_seconds=180,
            employee_notes="-2+3 & <script>\"alert\"\x07\x1f</script>",
        )
        self.followup_formula = self._create_followup(
            lead=self.lead_formula,
            owner=self.emp_alice,
            call=self.call_formula,
            scheduled_at=datetime(2026, 11, 13, 14, 0, 0, tzinfo=KOLKATA_TZ),
            status_val=FollowUp.Status.PENDING,
            notes="+SUM(A1:A10)\r=1",
        )

    def test_unit_exporter_formula_neutralization_and_numeric_preservation(self):
        # Genuine negative and positive numbers stay numeric
        self.assertEqual(sanitize_cell_value(-42), -42)
        self.assertEqual(sanitize_cell_value(-3.14), -3.14)
        self.assertEqual(sanitize_cell_value(0), 0)

        # Strings starting with formula triggers (or after leading spaces/newlines) are prefixed with "'"
        for dangerous in (
            "=1+1",
            "+1+1",
            "-1+1",
            "@SUM(A1)",
            "\t=1",
            "\r=1",
            "   =SUM(A1)",
            "\n-1+1",
        ):
            with self.subTest(dangerous=dangerous):
                sanitized = sanitize_cell_value(dangerous)
                self.assertIsInstance(sanitized, str)
                self.assertTrue(sanitized.startswith("'"))

        # Verify XLSX writes genuine negative numbers as t="n" without apostrophe prefix
        xlsx_bytes = render_xlsx_bytes(
            ["metric", "delta", "formula_text"],
            [("Net\x00Change", -125, "-125+1")],
        )
        _, typed_rows = parse_xlsx_rows(xlsx_bytes)
        self.assertEqual(typed_rows[1][0], ("inlineStr", "NetChange"))
        self.assertEqual(typed_rows[1][1], ("n", "-125"))
        self.assertEqual(typed_rows[1][2], ("inlineStr", "'-125+1"))

        # Verify CSV preserves genuine negative numbers while neutralizing formula strings
        csv_bytes = render_csv_bytes(
            ["metric", "delta", "formula_text"],
            [("Net Change", -125, "-125+1")],
        )
        parsed_csv = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8"))))
        self.assertEqual(parsed_csv[1], ["Net Change", "-125", "'-125+1"])

        # Filename sanitizer strips unsafe characters
        self.assertEqual(
            sanitize_filename('../../evil";filename="x.csv'),
            "evil_filename_x.csv",
        )

    def test_all_four_report_types_export_in_csv_and_xlsx_formats(self):
        report_types = ("employee_performance", "calls", "leads", "followups")
        forbidden_columns = {
            "password",
            "internal_notes",
            "idempotency_key",
            "request_fingerprint",
        }

        for rtype in report_types:
            for fmt in ("csv", "xlsx"):
                with self.subTest(report_type=rtype, export_format=fmt):
                    res = self.client.get(
                        EXPORT_URL,
                        {
                            "report_type": rtype,
                            "export_format": fmt,
                            "date_from": "2026-11-10",
                            "date_to": "2026-11-14",
                        },
                    )
                    self.assertEqual(res.status_code, status.HTTP_200_OK)
                    disposition = res["Content-Disposition"]
                    self.assertIn("attachment; filename=", disposition)
                    self.assertTrue(disposition.endswith(f'.{fmt}"'))

                    if fmt == "csv":
                        self.assertEqual(res["Content-Type"], CSV_CONTENT_TYPE)
                        text = res.content.decode("utf-8")
                        self.assertNotIn("CONFIDENTIAL_INTERNAL_NOTE_DO_NOT_EXPORT", text)
                        self.assertNotIn("alice.private@iqracentral.com", text)
                        rows = list(csv.reader(io.StringIO(text)))
                    else:
                        self.assertEqual(res["Content-Type"], XLSX_CONTENT_TYPE)
                        rows, _ = parse_xlsx_rows(res.content)

                    self.assertGreaterEqual(len(rows), 2)
                    headers = set(rows[0])
                    self.assertTrue(headers.isdisjoint(forbidden_columns))
                    if rtype == "employee_performance":
                        self.assertNotIn("email", headers)

    def test_exported_lead_call_and_followup_formula_injection_and_xml_escaping(self):
        # Leads XLSX export
        res_leads = self.client.get(
            EXPORT_URL,
            {
                "report_type": "leads",
                "export_format": "xlsx",
                "date_from": "2026-11-10",
                "date_to": "2026-11-14",
            },
        )
        self.assertEqual(res_leads.status_code, status.HTTP_200_OK)
        lead_rows, _ = parse_xlsx_rows(res_leads.content)
        header_idx = {name: i for i, name in enumerate(lead_rows[0])}
        data_row = lead_rows[1]
        self.assertEqual(data_row[header_idx["name"]], "'=CMD|' /C calc'!A0")
        self.assertEqual(data_row[header_idx["email"]], "'  @SUM(1,2)")
        self.assertEqual(
            data_row[header_idx["course_interest"]],
            "'\t=HYPERLINK(\"http://evil.example\")",
        )

        # Calls XLSX export (control chars \x00 and \x07 stripped, XML chars escaped & round-tripped, leading '-' neutralized)
        res_calls = self.client.get(
            EXPORT_URL,
            {
                "report_type": "calls",
                "export_format": "xlsx",
                "date_from": "2026-11-10",
                "date_to": "2026-11-14",
            },
        )
        self.assertEqual(res_calls.status_code, status.HTTP_200_OK)
        call_rows, call_typed = parse_xlsx_rows(res_calls.content)
        call_h = {name: i for i, name in enumerate(call_rows[0])}
        self.assertEqual(
            call_rows[1][call_h["employee_notes"]],
            "'-2+3 & <script>\"alert\"</script>",
        )
        self.assertEqual(
            call_typed[1][call_h["duration_seconds"]],
            ("n", "180"),
        )

        # Follow-ups CSV export
        res_fu = self.client.get(
            EXPORT_URL,
            {
                "report_type": "followups",
                "export_format": "csv",
                "date_from": "2026-11-10",
                "date_to": "2026-11-14",
            },
        )
        self.assertEqual(res_fu.status_code, status.HTTP_200_OK)
        fu_rows = list(csv.reader(io.StringIO(res_fu.content.decode("utf-8"))))
        fu_h = {name: i for i, name in enumerate(fu_rows[0])}
        self.assertEqual(fu_rows[1][fu_h["notes"]], "'+SUM(A1:A10)\r=1")

    @override_settings(REPORT_EXPORT_MAX_ROWS=2)
    def test_export_exceeding_max_rows_returns_400_without_truncating(self):
        # There are 3 non-superuser employees in setUp; limit=2 must reject with 400
        res = self.client.get(
            EXPORT_URL,
            {"report_type": "employee_performance", "export_format": "csv"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("maximum limit of 2 rows", str(res.data))


class ReportingQueryCountPerformanceTest(ReportingBaseTestCase):
    """Verify O(1) bounded PostgreSQL query counts without N+1 queries."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

        # Populate additional employees, leads, calls, and follow-ups
        for idx in range(5):
            emp = User.objects.create_user(
                username=f"perf_emp_{idx}",
                password="EmpPassword123!",
                role=User.Role.EMPLOYEE,
                first_name=f"Perf{idx}",
                last_name="Caller",
                is_active=True,
            )
            lead = self._create_lead(
                name=f"Perf Lead {idx}",
                phone=f"+91960000000{idx}",
                assigned_to=emp,
                created_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=KOLKATA_TZ),
            )
            self._create_call(
                lead=lead,
                caller=emp,
                outcome=self.outcome_interested,
                started_at=datetime(2026, 11, 12, 11, 0, 0, tzinfo=KOLKATA_TZ),
                duration_seconds=95,
            )
            self._create_followup(
                lead=lead,
                owner=emp,
                scheduled_at=datetime(2026, 11, 13, 12, 0, 0, tzinfo=KOLKATA_TZ),
            )

    def test_dashboard_executes_constant_number_of_queries(self):
        # 6 aggregate/group queries (Lead, Call summary, Call outcomes, Call daily trends, FollowUp, User+LeadAssignmentHistory=2 -> 7 queries total)
        with self.assertNumQueries(7):
            res = self.client.get(
                DASHBOARD_URL,
                {"date_from": "2026-11-10", "date_to": "2026-11-14"},
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        # With employee_id: +1 User.exists() validation query = 8 queries total
        with self.assertNumQueries(8):
            res_emp = self.client.get(
                DASHBOARD_URL,
                {
                    "date_from": "2026-11-10",
                    "date_to": "2026-11-14",
                    "employee_id": self.emp_alice.id,
                },
            )
            self.assertEqual(res_emp.status_code, status.HTTP_200_OK)

    def test_employee_performance_executes_constant_number_of_queries(self):
        # 1 COUNT(*) + 1 annotated SELECT with correlated subqueries = 2 queries regardless of employee count
        with self.assertNumQueries(2):
            res = self.client.get(
                EMPLOYEES_URL,
                {"date_from": "2026-11-10", "date_to": "2026-11-14"},
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertEqual(res.data["count"], 8)

    def test_exports_execute_constant_number_of_queries(self):
        # 1 COUNT(*) row-limit check + 1 SELECT query = 2 queries for each export type
        for rtype in ("employee_performance", "calls", "leads", "followups"):
            with self.subTest(report_type=rtype):
                with self.assertNumQueries(2):
                    res = self.client.get(
                        EXPORT_URL,
                        {
                            "report_type": rtype,
                            "export_format": "xlsx",
                            "date_from": "2026-11-10",
                            "date_to": "2026-11-14",
                        },
                    )
                    self.assertEqual(res.status_code, status.HTTP_200_OK)
