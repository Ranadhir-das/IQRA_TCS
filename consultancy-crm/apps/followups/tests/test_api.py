from datetime import datetime, time, timedelta, timezone as dt_timezone
import uuid
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallOutcome
from apps.followups.models import FollowUp, FollowUpEvent
from apps.followups.services import (
    cancel_follow_up,
    complete_follow_up,
    create_follow_up,
)
from apps.leads.models import Lead
from apps.leads.services import set_lead_assignment


KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


class FollowUpAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.list_url = reverse("followup-list-create")
        self.employee = User.objects.create_user(
            username="fu_api_employee",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            first_name="Follow",
            last_name="Employee",
        )
        self.other_employee = User.objects.create_user(
            username="fu_api_other",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            first_name="Other",
            last_name="Employee",
        )
        self.admin = User.objects.create_user(
            username="fu_api_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
            is_staff=True,
        )
        self.superuser_employee = User.objects.create_user(
            username="fu_api_super_emp",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            first_name="Super",
            last_name="Employee",
            is_staff=True,
            is_superuser=True,
        )
        self.inactive_employee = User.objects.create_user(
            username="fu_api_inactive_emp",
            password="SecureTestPass@123",
            role=User.Role.EMPLOYEE,
            is_active=False,
        )
        self.inactive_admin = User.objects.create_user(
            username="fu_api_inactive_admin",
            password="SecureTestPass@123",
            role=User.Role.ADMIN,
            is_active=False,
        )
        self.lead = Lead.objects.create(
            name="Primary Student",
            phone="9876508801",
            assigned_to=self.employee,
            internal_notes="Private admin lead note",
        )
        self.other_lead = Lead.objects.create(
            name="Other Student",
            phone="9876508802",
            assigned_to=self.other_employee,
            internal_notes="Other secret lead note",
        )
        self.outcome, _ = CallOutcome.objects.get_or_create(
            code="CALLBACK_REQ",
            defaults={"label": "Callback Requested"},
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.employee)

    def idempotency_headers(self, key=None):
        return {"HTTP_IDEMPOTENCY_KEY": str(key or uuid.uuid4())}

    def make_call(self, *, lead=None, caller=None):
        ended_at = timezone.now()
        return Call.objects.create(
            lead=lead or self.lead,
            caller=caller or self.employee,
            client_event_id=uuid.uuid4(),
            outcome=self.outcome,
            started_at=ended_at - timedelta(seconds=30),
            ended_at=ended_at,
            duration_seconds=30,
            request_fingerprint="c" * 64,
            employee_notes="Private call note",
        )

    def make_follow_up(
        self,
        *,
        owner=None,
        lead=None,
        call=None,
        scheduled_at=None,
        notes="Initial follow-up note",
        status=FollowUp.Status.PENDING,
    ):
        owner = owner or self.employee
        lead = lead or (self.lead if owner == self.employee else self.other_lead)
        follow_up = create_follow_up(
            actor=owner,
            lead_id=lead.pk,
            call_id=call.pk if call else None,
            scheduled_at=scheduled_at or (timezone.now() + timedelta(days=1)),
            notes=notes,
        )
        if status == FollowUp.Status.COMPLETED:
            return complete_follow_up(follow_up_id=follow_up.pk, actor=owner)
        if status == FollowUp.Status.CANCELLED:
            return cancel_follow_up(
                follow_up_id=follow_up.pk,
                actor=owner,
                reason="Cancelled in setup",
            )
        return follow_up

    def test_unauthenticated_and_inactive_users_are_rejected_across_endpoints(self):
        follow_up = self.make_follow_up()
        endpoints = (
            ("get", self.list_url, None),
            ("post", self.list_url, {}),
            ("get", reverse("followup-detail", args=[follow_up.pk]), None),
            ("post", reverse("followup-reschedule", args=[follow_up.pk]), {}),
            ("post", reverse("followup-complete", args=[follow_up.pk]), {}),
            ("post", reverse("followup-cancel", args=[follow_up.pk]), {}),
        )
        for method, url, data in endpoints:
            with self.subTest(user="anonymous", method=method, url=url):
                self.client.force_authenticate(user=None)
                response = getattr(self.client, method)(url, data=data, format="json")
                self.assertEqual(response.status_code, 401)

            for inactive in (self.inactive_employee, self.inactive_admin):
                with self.subTest(user=inactive.username, method=method, url=url):
                    self.authenticate(inactive)
                    response = getattr(self.client, method)(url, data=data, format="json")
                    self.assertEqual(response.status_code, 403)

    def test_generic_put_patch_and_delete_are_not_implemented(self):
        follow_up = self.make_follow_up()
        detail_url = reverse("followup-detail", args=[follow_up.pk])
        self.authenticate(self.employee)
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(self.list_url, {}, format="json").status_code, 405)
            self.assertEqual(getattr(self.client, method)(detail_url, {}, format="json").status_code, 405)
        self.assertEqual(self.client.post(detail_url, {}, format="json").status_code, 405)

    def test_admins_and_superuser_with_employee_role_cannot_create_reschedule_or_complete(self):
        follow_up = self.make_follow_up()
        schedule = (timezone.now() + timedelta(days=2)).isoformat()
        for admin_like in (self.admin, self.superuser_employee):
            with self.subTest(user=admin_like.username):
                self.authenticate(admin_like)
                create_resp = self.client.post(
                    self.list_url,
                    {"lead_id": self.lead.pk, "scheduled_at": schedule},
                    format="json",
                    **self.idempotency_headers(),
                )
                self.assertEqual(create_resp.status_code, 403)

                resched_resp = self.client.post(
                    reverse("followup-reschedule", args=[follow_up.pk]),
                    {"scheduled_at": schedule},
                    format="json",
                    **self.idempotency_headers(),
                )
                self.assertEqual(resched_resp.status_code, 403)

                complete_resp = self.client.post(
                    reverse("followup-complete", args=[follow_up.pk]),
                    {},
                    format="json",
                )
                self.assertEqual(complete_resp.status_code, 403)

    def test_employee_creates_followup_and_receives_exact_resource_contract(self):
        call = self.make_call()
        scheduled_at = (timezone.now() + timedelta(days=1)).astimezone(KOLKATA_TZ)
        key = uuid.uuid4()
        self.authenticate(self.employee)

        response = self.client.post(
            self.list_url,
            {
                "lead_id": self.lead.pk,
                "call_id": call.pk,
                "scheduled_at": scheduled_at.isoformat(),
                "notes": "  Call back tomorrow\r\nmorning  ",
            },
            format="json",
            **self.idempotency_headers(key),
        )
        self.assertEqual(response.status_code, 201, response.data)
        follow_up = FollowUp.objects.get()
        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "lead_id",
                "lead_name",
                "call_id",
                "scheduled_at",
                "status",
                "notes",
                "completed_at",
                "cancelled_at",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(response.data["id"], follow_up.pk)
        self.assertEqual(response.data["lead_id"], self.lead.pk)
        self.assertEqual(response.data["lead_name"], "Primary Student")
        self.assertEqual(response.data["call_id"], call.pk)
        self.assertEqual(response.data["status"], FollowUp.Status.PENDING)
        self.assertEqual(response.data["notes"], "Call back tomorrow\nmorning")
        self.assertIsNone(response.data["completed_at"])
        self.assertIsNone(response.data["cancelled_at"])
        for forbidden in (
            "owner",
            "owner_id",
            "owner_username_snapshot",
            "owner_name_snapshot",
            "request_fingerprint",
            "idempotency_key",
            "actor_username_snapshot",
            "internal_notes",
            "phone",
            "employee_notes",
        ):
            self.assertNotIn(forbidden, response.data)

        event = FollowUpEvent.objects.get(follow_up=follow_up)
        self.assertEqual(event.event_type, FollowUpEvent.EventType.CREATED)
        self.assertEqual(event.idempotency_key, key)
        self.assertEqual(len(event.request_fingerprint), 64)

    def test_create_rejects_unassigned_other_employee_and_archived_leads_with_generic_404(self):
        unassigned = Lead.objects.create(name="Unassigned Lead", phone="9876508803")
        archived = Lead.objects.create(
            name="Archived Lead",
            phone="9876508804",
            assigned_to=self.employee,
            is_archived=True,
        )
        schedule = (timezone.now() + timedelta(days=1)).isoformat()
        self.authenticate(self.employee)

        for target_lead in (unassigned, self.other_lead, archived):
            with self.subTest(lead_id=target_lead.pk):
                response = self.client.post(
                    self.list_url,
                    {"lead_id": target_lead.pk, "scheduled_at": schedule},
                    format="json",
                    **self.idempotency_headers(),
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    str(response.data["detail"]),
                    "Lead is not available for a follow-up.",
                )
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_create_rejects_invalid_call_ownership_or_lead_relationship(self):
        other_lead_call = self.make_call(lead=self.other_lead, caller=self.other_employee)
        other_caller_call = self.make_call(lead=self.lead, caller=self.other_employee)
        schedule = (timezone.now() + timedelta(days=1)).isoformat()
        self.authenticate(self.employee)

        for invalid_call_id in (other_lead_call.pk, other_caller_call.pk, 999999):
            with self.subTest(call_id=invalid_call_id):
                response = self.client.post(
                    self.list_url,
                    {
                        "lead_id": self.lead.pk,
                        "call_id": invalid_call_id,
                        "scheduled_at": schedule,
                    },
                    format="json",
                    **self.idempotency_headers(),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("call_id", response.data)
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_create_rejects_unknown_fields_missing_key_and_naive_timestamps(self):
        schedule = (timezone.now() + timedelta(days=1)).isoformat()
        self.authenticate(self.employee)

        for field in (
            "owner",
            "owner_id",
            "owner_username_snapshot",
            "status",
            "completed_at",
            "cancelled_at",
            "created_at",
            "idempotency_key",
            "request_fingerprint",
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    self.list_url,
                    {
                        "lead_id": self.lead.pk,
                        "scheduled_at": schedule,
                        field: "injected",
                    },
                    format="json",
                    **self.idempotency_headers(),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

        missing_key = self.client.post(
            self.list_url,
            {"lead_id": self.lead.pk, "scheduled_at": schedule},
            format="json",
        )
        self.assertEqual(missing_key.status_code, 400)
        self.assertIn("Idempotency-Key", missing_key.data)

        invalid_key = self.client.post(
            self.list_url,
            {"lead_id": self.lead.pk, "scheduled_at": schedule},
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-valid-uuid",
        )
        self.assertEqual(invalid_key.status_code, 400)
        self.assertIn("Idempotency-Key", invalid_key.data)

        naive_schedule = self.client.post(
            self.list_url,
            {"lead_id": self.lead.pk, "scheduled_at": "2026-11-20T10:30:00"},
            format="json",
            **self.idempotency_headers(),
        )
        self.assertEqual(naive_schedule.status_code, 400)
        self.assertIn("scheduled_at", naive_schedule.data)

    def test_create_idempotent_retry_and_conflicting_payload_behavior(self):
        call = self.make_call()
        key = uuid.uuid4()
        scheduled_at = (timezone.now() + timedelta(days=1)).replace(microsecond=123000)
        payload = {
            "lead_id": self.lead.pk,
            "call_id": call.pk,
            "scheduled_at": scheduled_at.astimezone(dt_timezone.utc).isoformat(),
            "notes": "Idempotent note",
        }
        self.authenticate(self.employee)

        first = self.client.post(
            self.list_url,
            payload,
            format="json",
            **self.idempotency_headers(key),
        )
        self.assertEqual(first.status_code, 201, first.data)

        # Even if lead is subsequently reassigned, exact retry still returns 200.
        set_lead_assignment(self.lead, self.other_employee, self.admin)
        retry = self.client.post(
            self.list_url,
            {
                **payload,
                "scheduled_at": scheduled_at.astimezone(KOLKATA_TZ).isoformat(),
            },
            format="json",
            **self.idempotency_headers(key),
        )
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data["id"], first.data["id"])
        self.assertEqual(FollowUp.objects.count(), 1)
        self.assertEqual(FollowUpEvent.objects.count(), 1)

        # Reusing the same key with any changed payload field returns 409.
        for changed in (
            {"lead_id": self.other_lead.pk},
            {"call_id": None},
            {"scheduled_at": (scheduled_at + timedelta(hours=1)).isoformat()},
            {"notes": "Changed note"},
        ):
            with self.subTest(changed=changed):
                conflict = self.client.post(
                    self.list_url,
                    {**payload, **changed},
                    format="json",
                    **self.idempotency_headers(key),
                )
                self.assertEqual(conflict.status_code, 409, conflict.data)

        # Another employee can use the same UUID independently.
        self.authenticate(self.other_employee)
        other_resp = self.client.post(
            self.list_url,
            {
                "lead_id": self.other_lead.pk,
                "scheduled_at": scheduled_at.isoformat(),
                "notes": "Other employee note",
            },
            format="json",
            **self.idempotency_headers(key),
        )
        self.assertEqual(other_resp.status_code, 201, other_resp.data)
        self.assertNotEqual(other_resp.data["id"], first.data["id"])

    def test_reschedule_lifecycle_idempotency_and_conflicts(self):
        follow_up = self.make_follow_up(notes="Keep these notes")
        other_follow_up = self.make_follow_up(owner=self.other_employee, lead=self.other_lead)
        url = reverse("followup-reschedule", args=[follow_up.pk])
        new_schedule = (timezone.now() + timedelta(days=3)).replace(microsecond=0)
        resched_key = uuid.uuid4()
        self.authenticate(self.employee)

        # Reschedule without notes preserves existing notes.
        first = self.client.post(
            url,
            {
                "scheduled_at": new_schedule.isoformat(),
                "reason": "Client asked for later date",
            },
            format="json",
            **self.idempotency_headers(resched_key),
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["notes"], "Keep these notes")
        self.assertEqual(
            FollowUpEvent.objects.filter(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.RESCHEDULED,
            ).count(),
            1,
        )

        # Exact retry returns 200 and creates no duplicate event.
        retry = self.client.post(
            url,
            {
                "scheduled_at": new_schedule.isoformat(),
                "reason": "Client asked for later date",
            },
            format="json",
            **self.idempotency_headers(resched_key),
        )
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(
            FollowUpEvent.objects.filter(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.RESCHEDULED,
            ).count(),
            1,
        )

        # Same key with changed schedule, notes, reason, follow-up ID, or operation returns 409.
        for changed in (
            {"scheduled_at": (new_schedule + timedelta(hours=2)).isoformat(), "reason": "Client asked for later date"},
            {"scheduled_at": new_schedule.isoformat(), "notes": "Keep these notes", "reason": "Client asked for later date"},
            {"scheduled_at": new_schedule.isoformat(), "reason": "Different reason"},
        ):
            with self.subTest(changed=changed):
                conflict = self.client.post(
                    url,
                    changed,
                    format="json",
                    **self.idempotency_headers(resched_key),
                )
                self.assertEqual(conflict.status_code, 409)

        # Reusing reschedule key on another follow-up or on create returns 409.
        cross_record = self.client.post(
            reverse("followup-reschedule", args=[other_follow_up.pk]),
            {"scheduled_at": new_schedule.isoformat()},
            format="json",
            **self.idempotency_headers(resched_key),
        )
        self.assertEqual(cross_record.status_code, 409)

        cross_op = self.client.post(
            self.list_url,
            {"lead_id": self.lead.pk, "scheduled_at": new_schedule.isoformat()},
            format="json",
            **self.idempotency_headers(resched_key),
        )
        self.assertEqual(cross_op.status_code, 409)

        # Fresh key on another employee's follow-up returns 404.
        other_resp = self.client.post(
            reverse("followup-reschedule", args=[other_follow_up.pk]),
            {"scheduled_at": new_schedule.isoformat()},
            format="json",
            **self.idempotency_headers(),
        )
        self.assertEqual(other_resp.status_code, 404)

    def test_completion_accepts_empty_object_only_and_is_terminal(self):
        call = self.make_call()
        follow_up = self.make_follow_up(call=call)
        complete_url = reverse("followup-complete", args=[follow_up.pk])
        self.authenticate(self.employee)

        # Non-empty body is rejected with 400.
        for invalid_body in (
            {"notes": "Updated during completion"},
            {"reason": "Completed"},
            {"status": "COMPLETED"},
        ):
            with self.subTest(body=invalid_body):
                bad_resp = self.client.post(complete_url, invalid_body, format="json")
                self.assertEqual(bad_resp.status_code, 400)

        before_lead = (self.lead.status, self.lead.assigned_to_id, self.lead.updated_at)
        before_call = (call.outcome_code_snapshot, call.employee_notes)

        resp = self.client.post(complete_url, {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], FollowUp.Status.COMPLETED)
        self.assertIsNotNone(resp.data["completed_at"])
        self.assertIsNone(resp.data["cancelled_at"])

        self.lead.refresh_from_db()
        call.refresh_from_db()
        self.assertEqual(
            (self.lead.status, self.lead.assigned_to_id, self.lead.updated_at),
            before_lead,
        )
        self.assertEqual(
            (call.outcome_code_snapshot, call.employee_notes),
            before_call,
        )
        self.assertTrue(
            FollowUpEvent.objects.filter(
                follow_up=follow_up,
                event_type=FollowUpEvent.EventType.COMPLETED,
                actor=self.employee,
            ).exists()
        )

        # Repeated complete, cancel, or reschedule returns 409.
        self.assertEqual(self.client.post(complete_url, {}, format="json").status_code, 409)
        self.assertEqual(
            self.client.post(
                reverse("followup-cancel", args=[follow_up.pk]),
                {},
                format="json",
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                reverse("followup-reschedule", args=[follow_up.pk]),
                {"scheduled_at": (timezone.now() + timedelta(days=2)).isoformat()},
                format="json",
                **self.idempotency_headers(),
            ).status_code,
            409,
        )

    def test_employee_and_admin_cancellation_policies(self):
        emp_follow_up = self.make_follow_up()
        admin_target = self.make_follow_up()

        # Employee can cancel own pending follow-up without reason.
        self.authenticate(self.employee)
        emp_cancel = self.client.post(
            reverse("followup-cancel", args=[emp_follow_up.pk]),
            {},
            format="json",
        )
        self.assertEqual(emp_cancel.status_code, 200, emp_cancel.data)
        self.assertEqual(emp_cancel.data["status"], FollowUp.Status.CANCELLED)
        self.assertIsNotNone(emp_cancel.data["cancelled_at"])
        self.assertEqual(
            self.client.post(
                reverse("followup-cancel", args=[emp_follow_up.pk]),
                {},
                format="json",
            ).status_code,
            409,
        )

        # Admin cannot cancel follow-up while owner is active.
        for admin_like in (self.admin, self.superuser_employee):
            with self.subTest(admin=admin_like.username):
                self.authenticate(admin_like)
                active_owner_resp = self.client.post(
                    reverse("followup-cancel", args=[admin_target.pk]),
                    {"reason": "Attempt while owner is active"},
                    format="json",
                )
                self.assertEqual(active_owner_resp.status_code, 403)

        # Deactivate the owner; admin cancellation now requires nonblank reason.
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        self.authenticate(self.admin)

        for blank_payload in ({}, {"reason": ""}, {"reason": "   "}):
            with self.subTest(payload=blank_payload):
                missing_reason = self.client.post(
                    reverse("followup-cancel", args=[admin_target.pk]),
                    blank_payload,
                    format="json",
                )
                self.assertEqual(missing_reason.status_code, 400)
                self.assertIn("reason", missing_reason.data)

        admin_cancel = self.client.post(
            reverse("followup-cancel", args=[admin_target.pk]),
            {"reason": "  Employee left organization  "},
            format="json",
        )
        self.assertEqual(admin_cancel.status_code, 200, admin_cancel.data)
        self.assertEqual(admin_cancel.data["status"], FollowUp.Status.CANCELLED)
        self.assertEqual(admin_cancel.data["owner_id"], self.employee.pk)
        self.assertEqual(admin_cancel.data["owner_username_snapshot"], "fu_api_employee")

        cancel_event = FollowUpEvent.objects.get(
            follow_up=admin_target,
            event_type=FollowUpEvent.EventType.CANCELLED,
        )
        self.assertEqual(cancel_event.actor_id, self.admin.pk)
        self.assertEqual(cancel_event.actor_username_snapshot, self.admin.username)
        self.assertEqual(cancel_event.reason, "Employee left organization")

        # Repeated admin cancellation returns 409.
        self.assertEqual(
            self.client.post(
                reverse("followup-cancel", args=[admin_target.pk]),
                {"reason": "Second attempt"},
                format="json",
            ).status_code,
            409,
        )

    def test_ownership_isolation_lead_reassignment_and_immutable_owner_snapshots(self):
        own_follow_up = self.make_follow_up()
        other_follow_up = self.make_follow_up(owner=self.other_employee, lead=self.other_lead)

        # Reassign self.lead to other_employee; own_follow_up must NOT transfer.
        set_lead_assignment(self.lead, self.other_employee, self.admin)

        # Rename employee later to verify admin response uses immutable snapshots.
        self.employee.first_name = "Mutated"
        self.employee.save(update_fields=["first_name"])

        # Original owner can still view and act on own_follow_up.
        self.authenticate(self.employee)
        list_resp = self.client.get(self.list_url)
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual([row["id"] for row in list_resp.data["results"]], [own_follow_up.pk])

        detail_resp = self.client.get(reverse("followup-detail", args=[own_follow_up.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertNotIn("owner_id", detail_resp.data)

        # other_employee cannot see or act on own_follow_up even though self.lead is now assigned to other_employee!
        self.authenticate(self.other_employee)
        self.assertEqual(
            self.client.get(reverse("followup-detail", args=[own_follow_up.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("followup-complete", args=[own_follow_up.pk]),
                {},
                format="json",
            ).status_code,
            404,
        )

        # Admin and superuser with EMPLOYEE role see both and receive immutable owner snapshots.
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])
        for admin_like in (self.admin, self.superuser_employee):
            with self.subTest(admin=admin_like.username):
                self.authenticate(admin_like)
                admin_list = self.client.get(self.list_url)
                self.assertEqual(admin_list.status_code, 200)
                self.assertEqual(
                    {row["id"] for row in admin_list.data["results"]},
                    {own_follow_up.pk, other_follow_up.pk},
                )
                admin_detail = self.client.get(
                    reverse("followup-detail", args=[own_follow_up.pk])
                )
                self.assertEqual(admin_detail.status_code, 200)
                self.assertEqual(admin_detail.data["owner_id"], self.employee.pk)
                self.assertEqual(
                    admin_detail.data["owner_username_snapshot"],
                    "fu_api_employee",
                )
                self.assertEqual(
                    admin_detail.data["owner_name_snapshot"],
                    "Follow Employee",
                )

    def test_kolkata_due_state_date_range_and_combined_filters(self):
        today_local = timezone.now().astimezone(KOLKATA_TZ).date()
        today_midnight = datetime.combine(today_local, time.min, tzinfo=KOLKATA_TZ)
        tomorrow_midnight = datetime.combine(
            today_local + timedelta(days=1),
            time.min,
            tzinfo=KOLKATA_TZ,
        )

        # Exact boundary records:
        # 1 microsecond before today's Asia/Kolkata midnight -> overdue
        overdue_edge = self.make_follow_up(
            scheduled_at=today_midnight - timedelta(microseconds=1)
        )
        # Exactly at today's Asia/Kolkata midnight -> today
        today_start_edge = self.make_follow_up(scheduled_at=today_midnight)
        # 1 microsecond before tomorrow's Asia/Kolkata midnight -> today
        today_end_edge = self.make_follow_up(
            scheduled_at=tomorrow_midnight - timedelta(microseconds=1)
        )
        # Exactly at tomorrow's Asia/Kolkata midnight -> upcoming
        upcoming_edge = self.make_follow_up(scheduled_at=tomorrow_midnight)
        # Terminal records scheduled today -> excluded from due_state
        completed_today = self.make_follow_up(
            scheduled_at=today_midnight + timedelta(hours=2),
            status=FollowUp.Status.COMPLETED,
        )
        cancelled_today = self.make_follow_up(
            scheduled_at=today_midnight + timedelta(hours=3),
            status=FollowUp.Status.CANCELLED,
        )
        other_emp_today = self.make_follow_up(
            owner=self.other_employee,
            lead=self.other_lead,
            scheduled_at=today_midnight + timedelta(hours=1),
        )

        self.authenticate(self.admin)

        overdue_resp = self.client.get(self.list_url, {"due_state": "overdue"})
        self.assertEqual(
            [row["id"] for row in overdue_resp.data["results"]],
            [overdue_edge.pk],
        )

        today_resp = self.client.get(self.list_url, {"due_state": "today"})
        self.assertEqual(
            [row["id"] for row in today_resp.data["results"]],
            [today_start_edge.pk, other_emp_today.pk, today_end_edge.pk],
        )

        upcoming_resp = self.client.get(self.list_url, {"due_state": "upcoming"})
        self.assertEqual(
            [row["id"] for row in upcoming_resp.data["results"]],
            [upcoming_edge.pk],
        )

        # Combined filters intersect: due_state=today & status=COMPLETED -> empty
        intersect_empty = self.client.get(
            self.list_url,
            {"due_state": "today", "status": FollowUp.Status.COMPLETED},
        )
        self.assertEqual(intersect_empty.data["results"], [])

        # Combined filters: employee_id + status + scheduled_from + scheduled_to
        combined = self.client.get(
            self.list_url,
            {
                "employee_id": self.employee.pk,
                "lead_id": self.lead.pk,
                "status": FollowUp.Status.COMPLETED,
                "scheduled_from": today_local.isoformat(),
                "scheduled_to": today_local.isoformat(),
            },
        )
        self.assertEqual(
            [row["id"] for row in combined.data["results"]],
            [completed_today.pk],
        )

        # Inclusive local date range excludes overdue_edge (yesterday) and upcoming_edge (tomorrow)
        today_range = self.client.get(
            self.list_url,
            {
                "scheduled_from": today_local.isoformat(),
                "scheduled_to": today_local.isoformat(),
            },
        )
        self.assertEqual(
            [row["id"] for row in today_range.data["results"]],
            [
                today_start_edge.pk,
                other_emp_today.pk,
                completed_today.pk,
                cancelled_today.pk,
                today_end_edge.pk,
            ],
        )

    def test_invalid_and_repeated_filters_and_employee_id_restriction(self):
        self.make_follow_up()
        self.authenticate(self.employee)

        emp_filter_resp = self.client.get(
            self.list_url,
            {"employee_id": self.employee.pk},
        )
        self.assertEqual(emp_filter_resp.status_code, 400)
        self.assertIn("employee_id", emp_filter_resp.data)

        self.authenticate(self.admin)
        invalid_queries = (
            "unknown_param=1",
            "status=INVALID",
            "due_state=later",
            "lead_id=0",
            "employee_id=0",
            "scheduled_from=not-a-date",
            "scheduled_to=2026-11-15T00:00:00Z",
            "scheduled_from=2026-11-16&scheduled_to=2026-11-15",
            "status=PENDING&status=COMPLETED",
            "due_state=today&due_state=overdue",
            "page_size=0",
            "page_size=abc",
            "page_size=10&page_size=20",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                resp = self.client.get(f"{self.list_url}?{query}")
                self.assertEqual(resp.status_code, 400, resp.data)

    def test_ordering_pagination_limits_and_bounded_query_counts(self):
        base_time = timezone.now().replace(microsecond=0) + timedelta(days=1)
        call = self.make_call()
        created = [
            self.make_follow_up(
                call=call if idx % 2 == 0 else None,
                scheduled_at=base_time + timedelta(minutes=idx // 2),
            )
            for idx in range(105)
        ]

        self.authenticate(self.employee)
        with self.assertNumQueries(2):
            default_page = self.client.get(self.list_url)
        self.assertEqual(default_page.status_code, 200)
        self.assertEqual(len(default_page.data["results"]), 20)
        self.assertEqual(default_page.data["count"], 105)

        capped_page = self.client.get(self.list_url, {"page_size": 500})
        self.assertEqual(capped_page.status_code, 200)
        self.assertEqual(len(capped_page.data["results"]), 100)

        expected_ids = [
            item.pk
            for item in sorted(created, key=lambda fu: (fu.scheduled_at, fu.pk))
        ]
        self.assertEqual(
            [row["id"] for row in capped_page.data["results"]],
            expected_ids[:100],
        )

        with self.assertNumQueries(1):
            detail = self.client.get(
                reverse("followup-detail", args=[created[0].pk])
            )
        self.assertEqual(detail.status_code, 200)

    def test_followup_event_history_endpoint(self):
        # Create a follow-up and perform a reschedule and admin cancellation
        initial_schedule = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        rescheduled_time = (timezone.now() + timedelta(days=3)).replace(microsecond=0)

        self.authenticate(self.employee)
        create_resp = self.client.post(
            self.list_url,
            {
                "lead_id": self.lead.pk,
                "scheduled_at": initial_schedule.isoformat(),
                "notes": "Initial schedule",
            },
            format="json",
            **self.idempotency_headers(),
        )
        self.assertEqual(create_resp.status_code, 201)
        follow_up_id = create_resp.data["id"]

        resched_resp = self.client.post(
            reverse("followup-reschedule", args=[follow_up_id]),
            {
                "scheduled_at": rescheduled_time.isoformat(),
                "notes": "Rescheduled notes",
                "reason": "Student requested Friday",
            },
            format="json",
            **self.idempotency_headers(),
        )
        self.assertEqual(resched_resp.status_code, 200)

        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])

        self.authenticate(self.admin)
        cancel_resp = self.client.post(
            reverse("followup-cancel", args=[follow_up_id]),
            {"reason": "Employee deactivated"},
            format="json",
        )
        self.assertEqual(cancel_resp.status_code, 200)

        events_url = reverse("followup-events", args=[follow_up_id])

        # Unauthenticated -> 401
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(events_url).status_code, 401)

        # Active and inactive employees and inactive admin -> 403
        for forbidden_user in (
            self.other_employee,
            self.inactive_employee,
            self.inactive_admin,
        ):
            with self.subTest(user=forbidden_user.username):
                self.authenticate(forbidden_user)
                self.assertEqual(self.client.get(events_url).status_code, 403)

        # Nonexistent follow-up -> 404
        self.authenticate(self.admin)
        self.assertEqual(
            self.client.get(reverse("followup-events", args=[9999999])).status_code,
            404,
        )

        # Rename actors after the fact to verify event history preserves immutable snapshots
        self.employee.first_name = "RenamedEmp"
        self.employee.save(update_fields=["first_name"])
        self.admin.first_name = "RenamedAdmin"
        self.admin.save(update_fields=["first_name"])

        # Active admin and superuser with EMPLOYEE role can inspect chronological events
        for admin_like in (self.admin, self.superuser_employee):
            with self.subTest(admin=admin_like.username):
                self.authenticate(admin_like)
                # Bounded query count: 1 exists check + 1 count + 1 page select = 3 queries
                with self.assertNumQueries(3):
                    resp = self.client.get(events_url)
                self.assertEqual(resp.status_code, 200, resp.data)
                results = resp.data["results"]
                self.assertEqual(len(results), 3)
                self.assertEqual(
                    [row["event_type"] for row in results],
                    [
                        FollowUpEvent.EventType.CREATED,
                        FollowUpEvent.EventType.RESCHEDULED,
                        FollowUpEvent.EventType.CANCELLED,
                    ],
                )
                expected_fields = {
                    "id",
                    "event_type",
                    "actor_id",
                    "actor_username_snapshot",
                    "actor_name_snapshot",
                    "previous_scheduled_at",
                    "new_scheduled_at",
                    "previous_status",
                    "new_status",
                    "reason",
                    "created_at",
                }
                for row in results:
                    self.assertEqual(set(row.keys()), expected_fields)
                    self.assertNotIn("idempotency_key", row)
                    self.assertNotIn("request_fingerprint", row)

                self.assertEqual(results[0]["actor_id"], self.employee.pk)
                self.assertEqual(results[0]["actor_name_snapshot"], "Follow Employee")
                self.assertEqual(results[1]["reason"], "Student requested Friday")
                self.assertEqual(results[2]["actor_id"], self.admin.pk)
                self.assertEqual(results[2]["actor_name_snapshot"], "Admin User")
                self.assertEqual(results[2]["reason"], "Employee deactivated")

        # Invalid or repeated query parameters return 400
        self.authenticate(self.admin)
        for bad_query in (
            "unknown=1",
            "page_size=0",
            "page_size=abc",
            "page_size=5&page_size=10",
            "page=1&page=2",
        ):
            with self.subTest(query=bad_query):
                self.assertEqual(
                    self.client.get(f"{events_url}?{bad_query}").status_code,
                    400,
                )
