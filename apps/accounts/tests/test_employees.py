"""
IQRA TCS Calling CRM — Employee Management Tests

Covers:
1.  GET  /api/v1/auth/employees/       — Admin lists employees.
2.  GET  /api/v1/auth/employees/       — Employee forbidden → 403.
3.  GET  /api/v1/auth/employees/       — Unauthenticated → 401.
4.  GET  /api/v1/auth/employees/?is_active=false — Inactive filter.
5.  POST /api/v1/auth/employees/       — Admin creates employee → 201.
6.  POST /api/v1/auth/employees/       — Duplicate username → 400.
7.  POST /api/v1/auth/employees/       — Duplicate email → 400.
8.  POST /api/v1/auth/employees/       — Weak password → 400.
9.  POST /api/v1/auth/employees/       — Missing required fields → 400.
10. POST /api/v1/auth/employees/       — Employee cannot create → 403.
11. GET  /api/v1/auth/employees/<pk>/  — Admin retrieves detail → 200.
12. GET  /api/v1/auth/employees/<pk>/  — Non-existent pk → 404.
13. PATCH /api/v1/auth/employees/<pk>/ — Admin updates name/phone → 200.
14. PATCH /api/v1/auth/employees/<pk>/ — Admin reactivates employee → 200.
15. PATCH /api/v1/auth/employees/<pk>/ — Employee role change rejected → 400.
16. PATCH /api/v1/auth/employees/<pk>/ — ADMIN record outside employee scope → 404.
17. DELETE /api/v1/auth/employees/<pk>/— Admin deactivates employee → 200.
18. DELETE /api/v1/auth/employees/<pk>/— Cannot deactivate already-inactive → 400.
19. Created employee has role=EMPLOYEE and is_active=True.
20. Created employee's password is hashed (not stored in plaintext).
"""

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User

LOGIN_URL = "/api/v1/auth/login/"
EMPLOYEES_URL = "/api/v1/auth/employees/"


def _url(pk):
    return f"/api/v1/auth/employees/{pk}/"


def _login_access(client, username, password="TestPass@1"):
    response = client.post(
        LOGIN_URL, {"username": username, "password": password}, format="json"
    )
    if response.status_code != status.HTTP_200_OK or "access" not in response.json():
        raise AssertionError(
            f"Login for {username!r} failed with HTTP {response.status_code}: "
            f"{response.content.decode(errors='replace')}"
        )
    return response.json()["access"]


def _make_user(username, role, password="TestPass@1", email=None, is_active=True):
    u = User(
        username=username,
        email=email or f"{username}@iqracentral.com",
        role=role,
        is_active=is_active,
        is_staff=(role == User.Role.ADMIN),
    )
    u.set_password(password)
    u.save()
    return u


class EmployeeListTests(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_user("admin", User.Role.ADMIN)
        self.emp1 = _make_user("emp1", User.Role.EMPLOYEE)
        self.emp2 = _make_user("emp2", User.Role.EMPLOYEE, is_active=False)

    def _admin_token(self):
        return _login_access(self.client, "admin")

    def _emp_token(self):
        return _login_access(self.client, "emp1")

    def test_admin_can_list_employees(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [e["username"] for e in resp.json()]
        self.assertIn("emp1", usernames)
        self.assertIn("emp2", usernames)
        # Admin should not appear in the EMPLOYEE list
        self.assertNotIn("admin", usernames)

    def test_employee_cannot_list_employees(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._emp_token()}")
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_list_employees(self):
        self.client.credentials()
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_filter_returns_only_inactive(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.get(EMPLOYEES_URL + "?is_active=false")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for emp in resp.json():
            self.assertFalse(emp["is_active"])

    def test_active_filter_returns_only_active(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.get(EMPLOYEES_URL + "?is_active=true")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for emp in resp.json():
            self.assertTrue(emp["is_active"])


class EmployeeCreateTests(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_user("admin", User.Role.ADMIN)
        self.emp = _make_user("existing_emp", User.Role.EMPLOYEE,
                              email="existing@iqracentral.com")

    def _admin_token(self):
        return _login_access(self.client, "admin")

    def _emp_token(self):
        return _login_access(self.client, "existing_emp")

    def _post(self, payload):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        return self.client.post(EMPLOYEES_URL, payload, format="json")

    def test_admin_creates_employee_successfully(self):
        resp = self._post({
            "username": "new_emp",
            "email": "new_emp@iqracentral.com",
            "first_name": "New",
            "last_name": "Employee",
            "password": "SecurePass@99",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["username"], "new_emp")
        self.assertEqual(data["role"], User.Role.EMPLOYEE)
        self.assertTrue(data["is_active"])

    def test_created_employee_role_is_always_employee(self):
        resp = self._post({
            "username": "sneaky",
            "email": "sneaky@iqracentral.com",
            "password": "SecurePass@99",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="sneaky")
        self.assertEqual(user.role, User.Role.EMPLOYEE)

    def test_created_employee_ignores_attempted_admin_role(self):
        resp = self._post({
            "username": "role_attempt",
            "email": "role_attempt@iqracentral.com",
            "password": "SecurePass@99",
            "role": User.Role.ADMIN,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="role_attempt")
        self.assertEqual(user.role, User.Role.EMPLOYEE)

    def test_created_employee_password_is_hashed(self):
        self._post({
            "username": "hashtest",
            "email": "hashtest@iqracentral.com",
            "password": "SecurePass@99",
        })
        user = User.objects.get(username="hashtest")
        self.assertNotEqual(user.password, "SecurePass@99")
        self.assertTrue(user.check_password("SecurePass@99"))

    def test_duplicate_username_returns_400(self):
        resp = self._post({
            "username": "existing_emp",
            "email": "other@iqracentral.com",
            "password": "SecurePass@99",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", resp.json())

    def test_duplicate_email_returns_400(self):
        resp = self._post({
            "username": "new_emp2",
            "email": "existing@iqracentral.com",
            "password": "SecurePass@99",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", resp.json())

    def test_weak_password_returns_400(self):
        resp = self._post({
            "username": "weakpass_emp",
            "email": "weakpass@iqracentral.com",
            "password": "1234",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_username_returns_400(self):
        resp = self._post({"email": "no_user@iqracentral.com", "password": "SecurePass@99"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", resp.json())

    def test_missing_password_returns_400(self):
        resp = self._post({"username": "no_pass_emp", "email": "nopass@iqracentral.com"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", resp.json())

    def test_employee_cannot_create_employee(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._emp_token()}")
        resp = self.client.post(EMPLOYEES_URL, {
            "username": "hacked",
            "email": "hacked@iqracentral.com",
            "password": "SecurePass@99",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class EmployeeDetailUpdateTests(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_user("admin", User.Role.ADMIN)
        self.emp = _make_user("emp1", User.Role.EMPLOYEE,
                              email="emp1@iqracentral.com")
        self.inactive_emp = _make_user("emp_inactive", User.Role.EMPLOYEE,
                                       is_active=False)

    def _admin_token(self):
        return _login_access(self.client, "admin")

    def _emp_token(self):
        return _login_access(self.client, "emp1")

    def test_admin_retrieves_employee_detail(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.get(_url(self.emp.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["username"], "emp1")

    def test_retrieve_nonexistent_pk_returns_404(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.get(_url(99999))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_updates_employee_name_and_phone(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.patch(_url(self.emp.pk),
                                 {"first_name": "Updated", "phone": "9876543210"},
                                 format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.first_name, "Updated")
        self.assertEqual(self.emp.phone, "9876543210")

    def test_admin_cannot_promote_employee_through_patch(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.patch(
            _url(self.emp.pk), {"role": User.Role.ADMIN}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", resp.json())
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.role, User.Role.EMPLOYEE)

    def test_admin_reactivates_inactive_employee(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.patch(_url(self.inactive_emp.pk),
                                 {"is_active": True},
                                 format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.inactive_emp.refresh_from_db()
        self.assertTrue(self.inactive_emp.is_active)

    def test_admin_account_is_outside_employee_detail_scope(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.patch(
            _url(self.admin.pk), {"is_active": False}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_employee_cannot_access_detail_endpoint(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._emp_token()}")
        resp = self.client.get(_url(self.emp.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class EmployeeDeactivateTests(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_user("admin", User.Role.ADMIN)
        self.emp = _make_user("emp1", User.Role.EMPLOYEE)
        self.emp_inactive = _make_user("emp_inactive", User.Role.EMPLOYEE, is_active=False)

    def _admin_token(self):
        return _login_access(self.client, "admin")

    def test_admin_deactivates_employee(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.delete(_url(self.emp.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.emp.refresh_from_db()
        self.assertFalse(self.emp.is_active)

    def test_deactivate_already_inactive_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.delete(_url(self.emp_inactive.pk))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already deactivated", resp.json()["detail"].lower())

    def test_deactivated_employee_cannot_login(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        self.client.delete(_url(self.emp.pk))
        self.client.credentials()
        resp = self.client.post(LOGIN_URL,
                                {"username": "emp1", "password": "TestPass@1"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("deactivated", str(resp.json()).lower())


class SuperuserIsolationAndPasswordResetTests(TestCase):
    """Tests for Phase 3.4 Step 1, Step 2 (admin reset), Step 4 (query validation), and Step 6 (workload)."""

    REFRESH_URL = "/api/v1/auth/refresh/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = _make_user("admin", User.Role.ADMIN)
        self.emp = _make_user("emp1", User.Role.EMPLOYEE)
        self.inactive_emp = _make_user(
            "emp_inactive",
            User.Role.EMPLOYEE,
            is_active=False,
        )
        self.superuser_emp = _make_user("superuser_emp", User.Role.EMPLOYEE)
        self.superuser_emp.is_superuser = True
        self.superuser_emp.is_staff = True
        self.superuser_emp.save(update_fields=["is_superuser", "is_staff"])

    def _admin_token(self):
        return _login_access(self.client, "admin")

    def _superuser_token(self):
        return _login_access(self.client, "superuser_emp")

    def _emp_token(self):
        return _login_access(self.client, "emp1")

    def test_superuser_with_employee_role_is_isolated_from_employee_endpoints(self):
        from types import SimpleNamespace
        from apps.accounts.api.permissions import IsAdminRole, IsEmployeeRole

        self.assertTrue(self.superuser_emp.is_admin)
        self.assertFalse(self.superuser_emp.is_employee)
        req = SimpleNamespace(user=self.superuser_emp)
        self.assertTrue(IsAdminRole().has_permission(req, None))
        self.assertFalse(IsEmployeeRole().has_permission(req, None))

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")

        # Excluded from employee list (including is_active filters)
        for query in ("", "?is_active=true", "?is_active=false"):
            list_resp = self.client.get(EMPLOYEES_URL + query)
            self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
            usernames = [row["username"] for row in list_resp.json()]
            self.assertNotIn("superuser_emp", usernames)

        # Detail GET, PATCH, DELETE, and reset-password all return 404 for superuser
        self.assertEqual(
            self.client.get(_url(self.superuser_emp.pk)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.patch(
                _url(self.superuser_emp.pk),
                {"first_name": "ShouldNotWork"},
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(_url(self.superuser_emp.pk)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(
                f"{_url(self.superuser_emp.pk)}reset-password/",
                {
                    "new_password": "ResetSuperuser@2026",
                    "confirm_password": "ResetSuperuser@2026",
                },
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.superuser_emp.refresh_from_db()
        self.assertTrue(self.superuser_emp.is_active)
        self.assertTrue(self.superuser_emp.check_password("TestPass@1"))

        # Superuser with EMPLOYEE role retains administrator access
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._superuser_token()}")
        su_list = self.client.get(EMPLOYEES_URL)
        self.assertEqual(su_list.status_code, status.HTTP_200_OK)

    def test_employee_list_query_parameter_validation(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        invalid_queries = (
            "?is_active=maybe",
            "?is_active=1",
            "?is_active=0",
            "?is_active=true&is_active=false",
            "?unknown_param=true",
            "?role=EMPLOYEE",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                resp = self.client.get(EMPLOYEES_URL + query)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_patch_rejects_unknown_fields(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        resp = self.client.patch(
            _url(self.emp.pk),
            {"first_name": "Valid", "is_superuser": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("is_superuser", resp.json())

    def test_admin_resets_employee_password_and_revokes_employee_refresh_tokens(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        emp_refresh_1 = RefreshToken.for_user(self.emp)
        emp_refresh_2 = RefreshToken.for_user(self.emp)
        admin_refresh = RefreshToken.for_user(self.admin)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")
        reset_url = f"{_url(self.emp.pk)}reset-password/"
        resp = self.client.post(
            reset_url,
            {
                "new_password": "AdminResetPass@2026",
                "confirm_password": "AdminResetPass@2026",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.json())
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.check_password("AdminResetPass@2026"))
        self.assertFalse(self.emp.check_password("TestPass@1"))

        # Target employee's existing refresh tokens are blacklisted
        self.client.credentials()
        for token in (emp_refresh_1, emp_refresh_2):
            refresh_resp = self.client.post(
                self.REFRESH_URL,
                {"refresh": str(token)},
                format="json",
            )
            self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # Admin's own refresh token remains valid
        admin_refresh_resp = self.client.post(
            self.REFRESH_URL,
            {"refresh": str(admin_refresh)},
            format="json",
        )
        self.assertEqual(admin_refresh_resp.status_code, status.HTTP_200_OK)

        # Employee can log in with the new password
        login_resp = self.client.post(
            LOGIN_URL,
            {"username": "emp1", "password": "AdminResetPass@2026"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)

    def test_admin_password_reset_permissions_and_validation(self):
        reset_url = f"{_url(self.emp.pk)}reset-password/"

        # Ordinary employee cannot reset passwords
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._emp_token()}")
        emp_attempt = self.client.post(
            reset_url,
            {
                "new_password": "HackedPassword@2026",
                "confirm_password": "HackedPassword@2026",
            },
            format="json",
        )
        self.assertEqual(emp_attempt.status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._admin_token()}")

        # Cannot reset ADMIN account or nonexistent account via employee reset endpoint
        for target_pk in (self.admin.pk, 999999):
            with self.subTest(target_pk=target_pk):
                not_found = self.client.post(
                    f"{_url(target_pk)}reset-password/",
                    {
                        "new_password": "AdminResetPass@2026",
                        "confirm_password": "AdminResetPass@2026",
                    },
                    format="json",
                )
                self.assertEqual(not_found.status_code, status.HTTP_404_NOT_FOUND)

        # Invalid payloads return 400
        for invalid_payload in (
            {"new_password": "123", "confirm_password": "123"},
            {
                "new_password": "ValidPassword@2026",
                "confirm_password": "MismatchPassword@2026",
            },
            {
                "new_password": "ValidPassword@2026",
                "confirm_password": "ValidPassword@2026",
                "extra": "not_allowed",
            },
        ):
            with self.subTest(payload=invalid_payload):
                bad_resp = self.client.post(reset_url, invalid_payload, format="json")
                self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_workload_counts_and_bounded_queries(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.followups.services import (
            cancel_follow_up,
            complete_follow_up,
            create_follow_up,
        )
        from apps.leads.models import Lead

        # emp1: 2 active leads, 1 archived lead; 2 pending follow-ups, 1 completed, 1 cancelled
        lead_1 = Lead.objects.create(
            name="Active Lead 1",
            phone="9876501001",
            assigned_to=self.emp,
            is_archived=False,
        )
        lead_2 = Lead.objects.create(
            name="Active Lead 2",
            phone="9876501002",
            assigned_to=self.emp,
            is_archived=False,
        )
        Lead.objects.create(
            name="Archived Lead",
            phone="9876501003",
            assigned_to=self.emp,
            is_archived=True,
        )
        now = timezone.now()
        create_follow_up(
            actor=self.emp,
            lead_id=lead_1.pk,
            scheduled_at=now + timedelta(days=1),
        )
        create_follow_up(
            actor=self.emp,
            lead_id=lead_2.pk,
            scheduled_at=now + timedelta(days=2),
        )
        fu_completed = create_follow_up(
            actor=self.emp,
            lead_id=lead_1.pk,
            scheduled_at=now + timedelta(days=3),
        )
        complete_follow_up(follow_up_id=fu_completed.pk, actor=self.emp)
        fu_cancelled = create_follow_up(
            actor=self.emp,
            lead_id=lead_2.pk,
            scheduled_at=now + timedelta(days=4),
        )
        cancel_follow_up(
            follow_up_id=fu_cancelled.pk,
            actor=self.emp,
            reason="No longer needed",
        )

        # Temporarily activate inactive_emp to create a pending follow-up through the service, then deactivate
        self.inactive_emp.is_active = True
        self.inactive_emp.save(update_fields=["is_active"])
        inactive_lead = Lead.objects.create(
            name="Inactive Owner Lead",
            phone="9876501004",
            assigned_to=self.inactive_emp,
            is_archived=False,
        )
        create_follow_up(
            actor=self.inactive_emp,
            lead_id=inactive_lead.pk,
            scheduled_at=now + timedelta(days=1),
        )
        self.inactive_emp.is_active = False
        self.inactive_emp.save(update_fields=["is_active"])

        self.client.force_authenticate(user=self.admin)

        # Employee list returns workload counts in a single SQL query (no N+1)
        with self.assertNumQueries(1):
            list_resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        by_username = {row["username"]: row for row in list_resp.json()}
        self.assertEqual(by_username["emp1"]["assigned_active_leads_count"], 2)
        self.assertEqual(by_username["emp1"]["pending_followups_count"], 2)
        self.assertEqual(by_username["emp_inactive"]["assigned_active_leads_count"], 1)
        self.assertEqual(by_username["emp_inactive"]["pending_followups_count"], 1)

        # Detail endpoint also returns workload counts in 1 query
        with self.assertNumQueries(1):
            detail_resp = self.client.get(_url(self.emp.pk))
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.json()["assigned_active_leads_count"], 2)
        self.assertEqual(detail_resp.json()["pending_followups_count"], 2)

        # Deactivation response includes workload counts so admins can see remaining work
        deact_resp = self.client.delete(_url(self.emp.pk))
        self.assertEqual(deact_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            deact_resp.json()["assigned_active_leads_count"],
            2,
        )
        self.assertEqual(
            deact_resp.json()["pending_followups_count"],
            2,
        )
